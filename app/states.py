from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.FAILED: {RunStatus.QUEUED},
    RunStatus.CANCELLED: {RunStatus.QUEUED},
    RunStatus.COMPLETED: set(),
}


def can_transition(current: str, target: str) -> bool:
    try:
        return RunStatus(target) in RUN_TRANSITIONS[RunStatus(current)]
    except ValueError:
        return False
