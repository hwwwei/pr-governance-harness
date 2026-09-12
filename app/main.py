import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .benchmark import build_cases, evaluate
from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .diffs import DiffParseError, parse_diff
from .evolution import activate as activate_version
from .evolution import propose as propose_version
from .evolution import rollback as rollback_version
from .github import GitHubClient, GitHubError
from .metrics import metrics_payload
from .models import BenchmarkCaseRecord, RunRecord
from .runtime import runtime
from .schemas import BenchmarkResult, EvolutionActivate, FeedbackCreate, ResumeRequest, RunCreate, RunDetail
from .store import Store


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        if not db.scalar(select(BenchmarkCaseRecord.id).limit(1)):
            for case in build_cases():
                db.add(BenchmarkCaseRecord(id=case.case_id, split=case.split, language=case.language, category=case.category, severity=case.severity, diff=case.diff, expected=list(case.expected)))
            db.commit()
    finally:
        db.close()
    await runtime.start()
    yield
    await runtime.stop()


app = FastAPI(title="PR Governance Multi-Agent Harness", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


def _run_json(run: RunRecord) -> dict:
    return {"id": run.id, "repository": run.repository, "pr_number": run.pr_number, "status": run.status, "risk_level": run.risk_level, "created_at": run.created_at}


def _run_detail(store: Store, run: RunRecord) -> dict:
    return {
        **_run_json(run),
        "report": run.report or {},
        "budget": run.budget or {},
        "checkpoint": run.checkpoint or {},
        "nodes": [{"node": n.node, "attempt": n.attempt, "status": n.status, "duration_ms": n.duration_ms, "output": n.output, "error": n.error} for n in store.list_nodes(run.id)],
        "trace": [{"event_type": e.event_type, "node": e.node, "payload": e.payload, "created_at": e.created_at} for e in store.list_trace(run.id)],
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    html = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "provider": get_settings().model_provider, "tools": runtime.tools.registered()}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(metrics_payload(), media_type="text/plain; version=0.0.4")


@app.post("/api/v1/runs", status_code=202)
async def create_run(payload: RunCreate, db: Session = Depends(get_db)) -> dict:
    if len(payload.diff) > 1_000_000:
        raise HTTPException(413, "diff is too large")
    try:
        parse_diff(payload.diff)
    except DiffParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    run = Store(db).create_run(payload.repository, payload.diff, payload.pr_number, payload.budget)
    try:
        await runtime.enqueue(run.id)
    except RuntimeError as exc:
        Store(db).set_run(run, status="failed", report={"error": str(exc), "trace_id": run.id})
        raise HTTPException(503, str(exc)) from exc
    return {**_run_json(run), "trace_id": run.id}


@app.get("/api/v1/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)) -> list[dict]:
    limit = max(1, min(limit, 100))
    runs = db.scalars(select(RunRecord).order_by(desc(RunRecord.created_at)).limit(limit)).all()
    return [_run_json(run) for run in runs]


@app.get("/api/v1/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    store = Store(db)
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return _run_detail(store, run)


@app.post("/api/v1/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = Store(db).get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(409, f"run is already terminal: {run.status}")
    if not Store(db).cancel_run(run_id):
        raise HTTPException(409, "run could not be cancelled")
    return {"id": run_id, "status": "cancelled"}


@app.post("/api/v1/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, payload: ResumeRequest | None = None, db: Session = Depends(get_db)) -> dict:
    run = Store(db).get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "only failed or cancelled runs can resume")
    requested = (payload.budget if payload else None) or {}
    budget = {**(run.budget or {})}
    for key, value in requested.items():
        if key.startswith("max_") and int(value) < int(budget.get(key, 0)):
            raise HTTPException(422, f"resume budget {key} cannot be lowered")
        budget[key] = value
    if budget.get("used_model_calls", 0) >= budget.get("max_model_calls", 12) and "max_model_calls" not in requested:
        raise HTTPException(409, "model call budget exhausted; provide a larger max_model_calls")
    if budget.get("used_seconds", 0) >= budget.get("max_seconds", 60) and "max_seconds" not in requested:
        raise HTTPException(409, "time budget exhausted; provide a larger max_seconds")
    Store(db).set_run(run, status="queued", budget=budget, execution_owner=None, lease_expires_at=None)
    await runtime.enqueue(run_id)
    return {"id": run_id, "status": "queued"}


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(run_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    if not Store(db).get_run(run_id):
        raise HTTPException(404, "run not found")

    async def stream():
        cursor = 0
        for _ in range(40):
            fresh = SessionLocal()
            try:
                item = Store(fresh).get_run(run_id)
                events = Store(fresh).list_trace(run_id)
                new_events = events[cursor:]
                cursor = len(events)
                yield f"data: {json.dumps({'status': item.status if item else 'missing', 'events': [{'event_type': event.event_type, 'node': event.node, 'payload': event.payload, 'created_at': event.created_at} for event in new_events]}, default=str)}\n\n"
                if not item or item.status in {"completed", "failed", "cancelled"}:
                    break
            finally:
                fresh.close()
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/v1/runs/{run_id}/feedback")
def feedback(run_id: str, payload: FeedbackCreate, db: Session = Depends(get_db)) -> dict:
    run = Store(db).get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    item = Store(db).add_feedback(run_id, run.repository, payload.label, payload.fingerprint, payload.note)
    return {"id": item.id, "label": item.label, "fingerprint": item.fingerprint}


@app.post("/api/v1/benchmarks/evaluate", response_model=BenchmarkResult)
def benchmark() -> dict:
    return evaluate()


@app.get("/api/v1/evolution")
def evolution(repository: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return [{"repository": x.repository, "version": x.version, "kind": x.kind, "status": x.status, "candidate": x.candidate, "validation": x.validation, "holdout": x.holdout, "predecessor_version": x.predecessor_version, "gate_decision": x.gate_decision, "created_at": x.created_at} for x in Store(db).list_versions(repository)]


@app.post("/api/v1/evolution/propose")
def propose_evolution(repository: str, db: Session = Depends(get_db)) -> dict:
    item = propose_version(repository, db, get_settings().auto_activate_evolution)
    return {"repository": item.repository, "version": item.version, "status": item.status, "validation": item.validation, "holdout": item.holdout}


@app.post("/api/v1/evolution/activate")
def activate_evolution(payload: EvolutionActivate, db: Session = Depends(get_db)) -> dict:
    item = activate_version(payload.version, db)
    if not item:
        raise HTTPException(409, "evolution version not found or did not pass the gate")
    return {"version": item.version, "status": item.status}


@app.post("/api/v1/evolution/rollback")
def rollback_evolution(repository: str, db: Session = Depends(get_db)) -> dict:
    item = rollback_version(repository, db)
    if not item:
        raise HTTPException(404, "no evolution version found")
    return {"version": item.version, "status": item.status}


@app.post("/api/v1/github/webhook")
async def github_webhook(request: Request, x_hub_signature_256: str | None = Header(default=None), x_github_delivery: str | None = Header(default=None)) -> JSONResponse:
    body = await request.body()
    settings = get_settings()
    if not GitHubClient.verify_signature(body, x_hub_signature_256, settings.github_webhook_secret):
        raise HTTPException(401, "invalid webhook signature")
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid JSON webhook payload") from exc
    if payload.get("action") not in {"opened", "reopened", "synchronize"}:
        return JSONResponse({"accepted": True, "ignored": True})
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {}).get("full_name")
    number = pr.get("number") or payload.get("number")
    if not repo or not number:
        raise HTTPException(400, "webhook lacks repository or pull request number")
    if not x_github_delivery:
        raise HTTPException(400, "X-GitHub-Delivery header is required")
    delivery_store = Store(SessionLocal())
    try:
        if not delivery_store.record_webhook_delivery(x_github_delivery, repo, str(payload.get("action", "unknown"))):
            previous = delivery_store.get_webhook_delivery(x_github_delivery)
            return JSONResponse({"accepted": True, "duplicate": True, "run_id": previous.run_id if previous else None})
    finally:
        delivery_store.db.close()
    try:
        data = GitHubClient(settings.github_token).fetch_pull_request(repo, int(number))
    except GitHubError as exc:
        raise HTTPException(502, str(exc)) from exc
    db = SessionLocal()
    try:
        run = Store(db).create_run(repo, data["diff"], int(number), {"max_model_calls": 12, "max_retries": 1, "max_seconds": 60})
        Store(db).attach_delivery_run(x_github_delivery, run.id)
    finally:
        db.close()
    try:
        await runtime.enqueue(run.id)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return JSONResponse({"accepted": True, "run_id": run.id}, status_code=202)


@app.post("/api/v1/github/runs", status_code=202)
async def github_run(repository: str, number: int, db: Session = Depends(get_db)) -> dict:
    if number < 1 or not repository.strip():
        raise HTTPException(400, "repository and positive pull request number are required")
    try:
        data = GitHubClient(get_settings().github_token).fetch_pull_request(repository, number)
    except GitHubError as exc:
        raise HTTPException(502, str(exc)) from exc
    run = Store(db).create_run(repository, data["diff"], number, {"max_model_calls": 12, "max_retries": 1, "max_seconds": 60})
    try:
        await runtime.enqueue(run.id)
    except RuntimeError as exc:
        Store(db).set_run(run, status="failed", report={"error": str(exc), "trace_id": run.id})
        raise HTTPException(503, str(exc)) from exc
    return {**_run_json(run), "trace_id": run.id}
