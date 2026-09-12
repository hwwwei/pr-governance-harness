from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class RunRecord(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    repository: Mapped[str] = mapped_column(String(255), index=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    diff: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(32), default="unknown")
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    budget: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class NodeExecutionRecord(Base):
    __tablename__ = "node_executions"
    __table_args__ = (UniqueConstraint("run_id", "node", "attempt", name="uq_run_node_attempt"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    node: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TraceEventRecord(Base):
    __tablename__ = "trace_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FeedbackRecord(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    repository: Mapped[str] = mapped_column(String(255), index=True)
    label: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryPatternRecord(Base):
    __tablename__ = "memory_patterns"
    __table_args__ = (UniqueConstraint("repository", "fingerprint", name="uq_memory_repository_fingerprint"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository: Mapped[str] = mapped_column(String(255), index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(64))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    examples: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvolutionVersionRecord(Base):
    __tablename__ = "evolution_versions"
    __table_args__ = (UniqueConstraint("repository", "version", name="uq_evolution_repository_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository: Mapped[str] = mapped_column(String(255), index=True)
    version: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), default="prompt")
    status: Mapped[str] = mapped_column(String(32), default="candidate")
    candidate: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    holdout: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    predecessor_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gate_decision: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WebhookDeliveryRecord(Base):
    __tablename__ = "webhook_deliveries"
    delivery_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    repository: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BenchmarkCaseRecord(Base):
    __tablename__ = "benchmark_cases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    split: Mapped[str] = mapped_column(String(16), index=True)
    language: Mapped[str] = mapped_column(String(32), default="unknown")
    category: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="none")
    diff: Mapped[str] = mapped_column(Text)
    expected: Mapped[list[Any]] = mapped_column(JSON, default=list)
