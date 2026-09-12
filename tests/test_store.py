from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RunRecord
from app.store import Store


def test_run_claim_is_atomic_and_terminal_finish_is_owner_checked():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        run = Store(session).create_run("demo/repo", "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,0 +1,1 @@\n+value = 1\n", None, {"max_model_calls": 2, "max_seconds": 10})
        first = Store(session).claim_run(run.id, "worker-a")
        second = Store(session).claim_run(run.id, "worker-b")
        assert first is not None
        assert second is None
        assert Store(session).finish_run(run.id, "worker-b", status="completed") is False
        assert Store(session).finish_run(run.id, "worker-a", status="completed") is True
        assert session.get(RunRecord, run.id).status == "completed"
    finally:
        session.close()
        engine.dispose()


def test_webhook_delivery_id_is_idempotent():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        store = Store(session)
        assert store.record_webhook_delivery("delivery-1", "demo/repo", "opened") is True
        assert store.record_webhook_delivery("delivery-1", "demo/repo", "opened") is False
    finally:
        session.close()
        engine.dispose()


def test_cancel_is_collaborative_and_terminal_finish_cannot_resurrect_run():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        store = Store(session)
        run = store.create_run("demo/repo", "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,0 +1,1 @@\n+value = 1\n", None, {"max_model_calls": 2, "max_seconds": 10})
        assert store.cancel_run(run.id) is True
        assert store.cancel_run(run.id) is False
        assert session.get(RunRecord, run.id).status == "cancelled"
        assert store.finish_run(run.id, "worker-a", status="completed") is False
        assert session.get(RunRecord, run.id).status == "cancelled"
    finally:
        session.close()
        engine.dispose()
