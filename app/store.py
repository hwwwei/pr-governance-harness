from typing import Any

from datetime import datetime, timedelta, timezone
from sqlalchemy import desc, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import EvolutionVersionRecord, FeedbackRecord, MemoryPatternRecord, NodeExecutionRecord, RunRecord, TraceEventRecord, WebhookDeliveryRecord, now
from .states import can_transition


class Store:
    def __init__(self, db: Session):
        self.db = db

    def create_run(self, repository: str, diff: str, pr_number: int | None, budget: dict[str, Any]) -> RunRecord:
        run = RunRecord(repository=repository, diff=diff, pr_number=pr_number, budget=budget)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.db.get(RunRecord, run_id)

    def set_run(self, run: RunRecord, **fields: Any) -> None:
        if "status" in fields and fields["status"] != run.status and not can_transition(run.status, fields["status"]):
            raise ValueError(f"illegal run transition: {run.status} -> {fields['status']}")
        for key, value in fields.items():
            setattr(run, key, value)
        run.updated_at = now()
        self.db.add(run)
        self.db.commit()

    def claim_run(self, run_id: str, owner: str, lease_seconds: int = 90) -> RunRecord | None:
        """Atomically claim a queued or expired run for one worker."""
        now_value = now()
        expiry = now_value + timedelta(seconds=lease_seconds)
        statement = (
            update(RunRecord)
            .where(
                RunRecord.id == run_id,
                or_(
                    RunRecord.status == "queued",
                    (RunRecord.status == "running")
                    & (RunRecord.lease_expires_at.is_(None) | (RunRecord.lease_expires_at < now_value)),
                ),
            )
            .values(status="running", execution_owner=owner, lease_expires_at=expiry, updated_at=now_value)
        )
        result = self.db.execute(statement)
        self.db.commit()
        if result.rowcount != 1:
            return None
        return self.get_run(run_id)

    def renew_lease(self, run_id: str, owner: str, lease_seconds: int = 90) -> None:
        self.db.execute(
            update(RunRecord)
            .where(RunRecord.id == run_id, RunRecord.execution_owner == owner, RunRecord.status == "running")
            .values(lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_seconds), updated_at=now())
        )
        self.db.commit()

    def next_attempt(self, run_id: str, name: str) -> int:
        attempts = self.db.scalars(select(NodeExecutionRecord.attempt).where(NodeExecutionRecord.run_id == run_id, NodeExecutionRecord.node == name)).all()
        return max(attempts, default=0) + 1

    def trace(self, run_id: str, event_type: str, node: str | None = None, **payload: Any) -> None:
        self.db.add(TraceEventRecord(run_id=run_id, event_type=event_type, node=node, payload=payload))
        self.db.commit()

    def node(self, run_id: str, name: str, attempt: int, status: str = "running", **kwargs: Any) -> NodeExecutionRecord:
        existing = self.db.scalar(select(NodeExecutionRecord).where(NodeExecutionRecord.run_id == run_id, NodeExecutionRecord.node == name, NodeExecutionRecord.attempt == attempt))
        if existing:
            return existing
        item = NodeExecutionRecord(run_id=run_id, node=name, attempt=attempt, status=status, **kwargs)
        self.db.add(item)
        self.db.commit()
        return item

    def record_webhook_delivery(self, delivery_id: str, repository: str, action: str) -> bool:
        item = WebhookDeliveryRecord(delivery_id=delivery_id, repository=repository, action=action)
        self.db.add(item)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return False
        return True

    def attach_delivery_run(self, delivery_id: str, run_id: str) -> None:
        item = self.db.get(WebhookDeliveryRecord, delivery_id)
        if item:
            item.run_id = run_id
            self.db.add(item)
            self.db.commit()

    def get_webhook_delivery(self, delivery_id: str) -> WebhookDeliveryRecord | None:
        return self.db.get(WebhookDeliveryRecord, delivery_id)

    def cancel_run(self, run_id: str) -> bool:
        result = self.db.execute(
            update(RunRecord)
            .where(RunRecord.id == run_id, RunRecord.status.in_(["queued", "running"]))
            .values(status="cancelled", updated_at=now())
        )
        self.db.commit()
        return result.rowcount == 1

    def finish_run(self, run_id: str, owner: str, **fields: Any) -> bool:
        fields.update({"execution_owner": None, "lease_expires_at": None, "updated_at": now()})
        result = self.db.execute(
            update(RunRecord)
            .where(RunRecord.id == run_id, RunRecord.status == "running", RunRecord.execution_owner == owner)
            .values(**fields)
        )
        self.db.commit()
        return result.rowcount == 1

    def finish_node(self, item: NodeExecutionRecord, status: str, output: dict[str, Any] | None = None, error: str | None = None, duration_ms: float = 0) -> None:
        item.status = status
        item.output = output or {}
        item.error = error
        item.duration_ms = duration_ms
        item.finished_at = now()
        self.db.add(item)
        self.db.commit()

    def list_nodes(self, run_id: str) -> list[NodeExecutionRecord]:
        return list(self.db.scalars(select(NodeExecutionRecord).where(NodeExecutionRecord.run_id == run_id).order_by(NodeExecutionRecord.id)))

    def list_trace(self, run_id: str) -> list[TraceEventRecord]:
        return list(self.db.scalars(select(TraceEventRecord).where(TraceEventRecord.run_id == run_id).order_by(TraceEventRecord.id)))

    def add_feedback(self, run_id: str, repository: str, label: str, fingerprint: str, note: str | None) -> FeedbackRecord:
        item = FeedbackRecord(run_id=run_id, repository=repository, label=label, fingerprint=fingerprint, note=note)
        self.db.add(item)
        pattern = self.db.scalar(select(MemoryPatternRecord).where(MemoryPatternRecord.repository == repository, MemoryPatternRecord.fingerprint == fingerprint))
        if pattern:
            pattern.occurrences += 1
            pattern.examples = [*(pattern.examples or [])[-4:], {"label": label, "note": note}]
            self.db.add(pattern)
        else:
            self.db.add(MemoryPatternRecord(repository=repository, fingerprint=fingerprint, category=label, occurrences=1, examples=[{"label": label, "note": note}]))
        self.db.commit()
        return item

    def list_feedback(self, repository: str) -> list[FeedbackRecord]:
        return list(self.db.scalars(select(FeedbackRecord).where(FeedbackRecord.repository == repository).order_by(FeedbackRecord.id)))

    def list_versions(self, repository: str | None = None) -> list[EvolutionVersionRecord]:
        stmt = select(EvolutionVersionRecord).order_by(desc(EvolutionVersionRecord.created_at))
        if repository:
            stmt = stmt.where(EvolutionVersionRecord.repository == repository)
        return list(self.db.scalars(stmt))

    def active_version(self, repository: str) -> EvolutionVersionRecord | None:
        return self.db.scalar(
            select(EvolutionVersionRecord)
            .where(
                EvolutionVersionRecord.repository == repository,
                EvolutionVersionRecord.status == "active",
            )
            .order_by(desc(EvolutionVersionRecord.created_at))
        )
