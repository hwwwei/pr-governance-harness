from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from .benchmark import evaluate
from .metrics import EVOLUTION_GATE
from .models import EvolutionVersionRecord
from .store import Store


def passes_gate(metrics: dict, baseline: dict) -> bool:
    if not baseline:
        return True
    gate_keys = ("f1", "high_risk_recall", "clean_pr_specificity")
    return all(metrics.get(key, 0) >= baseline.get(key, 0) for key in gate_keys) and any(
        metrics.get(key, 0) > baseline.get(key, 0) for key in gate_keys
    )


def _metrics_for_active(active: EvolutionVersionRecord | None, split: str) -> dict:
    if active:
        payload = active.validation if split == "validation" else active.holdout
        if payload and isinstance(payload.get("metrics"), dict):
            return payload["metrics"]
    return evaluate(split=split)["metrics"]


def propose(repository: str, db: Session, auto_activate: bool = True) -> EvolutionVersionRecord:
    store = Store(db)
    active = store.active_version(repository)
    feedback = store.list_feedback(repository)
    disabled = sorted({item.fingerprint for item in feedback if item.label == "false_positive"})
    candidate = {
        "summary": "根据仓库级反馈收紧证据要求并抑制重复误报",
        "source": "feedback+validation",
        "feedback_count": len(feedback),
        "failure_patterns": sorted({item.fingerprint for item in feedback}),
        "disabled_fingerprints": disabled,
        "prompt_suffix": "Only report a finding when the added-line evidence is specific and actionable.",
    }
    baseline_validation = active.validation.get("metrics", {}) if active else {}
    baseline_holdout = active.holdout.get("metrics", {}) if active else {}
    validation_metrics = evaluate(split="validation", candidate=candidate)["metrics"]
    validation_passed = passes_gate(validation_metrics, baseline_validation)
    holdout_metrics = evaluate(split="holdout", candidate=candidate)["metrics"] if validation_passed else {}
    holdout_passed = passes_gate(holdout_metrics, baseline_holdout) if validation_passed else False
    passed = validation_passed and holdout_passed
    version = f"v{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
    item = EvolutionVersionRecord(
        repository=repository,
        version=version,
        kind="prompt+skill",
        status="candidate" if passed else "rejected",
        candidate=candidate,
        validation={"metrics": validation_metrics, "baseline": baseline_validation, "passed": validation_passed},
        holdout={"metrics": holdout_metrics, "baseline": baseline_holdout, "passed": holdout_passed},
        predecessor_version=active.version if active else None,
        gate_decision="passed" if passed else "rejected",
    )
    if auto_activate and passed:
        if active:
            active.status = "rolled_back"
            db.add(active)
        item.status = "active"
    db.add(item)
    db.commit()
    EVOLUTION_GATE.labels("passed" if passed else "rejected").inc()
    return item


def activate(version: str, db: Session) -> EvolutionVersionRecord | None:
    item = db.query(EvolutionVersionRecord).filter(EvolutionVersionRecord.version == version).first()
    if not item or item.gate_decision != "passed":
        return None
    db.query(EvolutionVersionRecord).filter(EvolutionVersionRecord.repository == item.repository, EvolutionVersionRecord.status == "active").update({"status": "rolled_back"})
    item.status = "active"
    db.add(item)
    db.commit()
    return item


def rollback(repository: str, db: Session) -> EvolutionVersionRecord | None:
    store = Store(db)
    active = store.active_version(repository)
    if not active or not active.predecessor_version:
        return None
    previous = db.query(EvolutionVersionRecord).filter(
        EvolutionVersionRecord.repository == repository,
        EvolutionVersionRecord.version == active.predecessor_version,
    ).first()
    if not previous:
        return None
    active.status = "rolled_back"
    previous.status = "active"
    db.add_all([active, previous])
    db.commit()
    return previous
