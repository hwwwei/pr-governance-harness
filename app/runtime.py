import asyncio
import time
from contextlib import suppress
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .config import get_settings
from .db import SessionLocal
from .diffs import changed_files
from .metrics import ACTIVE_RUNS, BUDGET_USED, FINDINGS, MODEL_CALLS, NODE_DURATION, NODE_RETRIES, RUNS, TOOL_CALLS
from .providers import AgentProvider, build_provider
from .stream import RedisStreamBus
from .store import Store
from .tools import ToolRegistry


class BudgetExceeded(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


class RuntimeManager:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider: AgentProvider = build_provider(self.settings)
        self.tools = ToolRegistry()
        self.stream = RedisStreamBus(self.settings.redis_url)
        # asyncio primitives are bound to the loop that first awaits them.  The
        # FastAPI TestClient (and some embedding applications) can create more
        # than one lifespan loop, so create the local queue per runtime start.
        self.queue: asyncio.Queue[str] | None = None
        self.task: asyncio.Task[None] | None = None
        self.shutdown_event = asyncio.Event()

    async def start(self) -> None:
        if not self.settings.run_in_process:
            return
        if not self.task:
            self.queue = asyncio.Queue()
            self.shutdown_event.clear()
            self.task = asyncio.create_task(self._worker(), name="harness-runtime")

    async def stop(self) -> None:
        self.shutdown_event.set()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        self.queue = None
        await self.stream.close()

    async def enqueue(self, run_id: str) -> None:
        published = await self.stream.publish(run_id)
        if not published and not self.settings.run_in_process:
            raise RuntimeError("Redis Stream is unavailable while RUN_IN_PROCESS=false")
        if self.settings.run_in_process:
            if self.queue is None:
                raise RuntimeError("runtime is not started")
            await self.queue.put(run_id)

    async def _worker(self) -> None:
        queue = self.queue
        if queue is None:
            return
        while not self.shutdown_event.is_set():
            try:
                run_id = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                await self.execute(run_id)
            finally:
                queue.task_done()

    async def execute(self, run_id: str) -> None:
        owner = f"worker-{uuid4()}"
        db = SessionLocal()
        store = Store(db)
        run = store.get_run(run_id)
        if not run or run.status in {"completed", "cancelled"}:
            db.close()
            return
        run = store.claim_run(run_id, owner)
        if not run:
            db.close()
            return

        budget = {"max_model_calls": 12, "max_retries": 1, "max_seconds": 60, **(run.budget or {})}
        budget.setdefault("used_model_calls", 0)
        budget.setdefault("used_seconds", 0.0)
        store.set_run(run, budget=budget)
        active_version = store.active_version(run.repository)
        evolution_context = {
            "version": active_version.version,
            "kind": active_version.kind,
            "candidate": active_version.candidate,
        } if active_version else None
        store.trace(run_id, "run_started", budget=budget, evolution_version=active_version.version if active_version else None)
        ACTIVE_RUNS.inc()
        try:
            checkpoint = run.checkpoint or {}
            saved_nodes = dict(checkpoint.get("nodes", {}))
            if checkpoint.get("node") and "data" in checkpoint:
                saved_nodes.setdefault(checkpoint["node"], checkpoint["data"])

            def save_checkpoint(node: str, data: dict[str, Any]) -> None:
                saved_nodes[node] = data
                run.checkpoint = {"last_node": node, "nodes": saved_nodes}
                store.set_run(run, checkpoint=run.checkpoint, budget=budget)
                store.renew_lease(run_id, owner)

            ingest = saved_nodes.get("ingest") or await self._node(
                store, run_id, "ingest", "planner",
                lambda: {"files": changed_files(run.diff), "lines": len(run.diff.splitlines())}, budget,
            )
            save_checkpoint("ingest", ingest)
            plan = saved_nodes.get("plan") or await self._node(
                store, run_id, "plan", "planner", lambda: self._plan(run.diff), budget,
            )
            save_checkpoint("plan", plan)

            async def call_agent(role: str) -> dict[str, Any]:
                if role in saved_nodes:
                    return saved_nodes[role]

                async def provider_action() -> dict[str, Any]:
                    self._reserve_call(budget)
                    MODEL_CALLS.labels(self.settings.model_provider, role).inc()
                    BUDGET_USED.labels("model_calls").inc()
                    return await asyncio.to_thread(
                        self.provider.analyze,
                        role,
                        run.diff,
                        {"plan": plan, "repository": run.repository, "evolution": evolution_context},
                    )

                result = await self._node(store, run_id, role, role, provider_action, budget)
                save_checkpoint(role, result)
                return result

            roles = plan["roles"]
            parallel = await asyncio.gather(*(call_agent(role) for role in roles))
            agent_results = {role: result for role, result in zip(roles, parallel)}
            for result in agent_results.values():
                for finding in result.get("findings", []):
                    FINDINGS.labels(finding.get("kind", "quality"), finding.get("severity", "low")).inc()

            aggregate = saved_nodes.get("aggregate") or await self._node(
                store, run_id, "aggregate", "planner", lambda: self._aggregate(agent_results), budget,
            )
            save_checkpoint("aggregate", aggregate)

            originals: dict[str, dict[str, Any]] = {}
            for finding in aggregate["findings"]:
                key = finding.get("fingerprint") or finding.get("id")
                review_id = f"review-{key}"
                normalized = {**finding, "review_id": review_id}
                originals[key] = normalized
                originals[review_id] = normalized
            accepted: list[dict[str, Any]] = []
            critic = saved_nodes.get("blind_critic")
            for round_no in range(0, 3):
                node_name = "blind_critic" if round_no == 0 else f"blind_critic_{round_no}"
                candidates = list({item["review_id"]: item for item in originals.values()}.values())
                critic_input = [{k: v for k, v in f.items() if k not in {"source_stage", "id"}} for f in candidates]
                if round_no > 0 or critic is None:
                    async def critic_action() -> dict[str, Any]:
                        self._reserve_call(budget)
                        MODEL_CALLS.labels(self.settings.model_provider, "critic").inc()
                        BUDGET_USED.labels("model_calls").inc()
                        return await asyncio.to_thread(self.provider.critic, critic_input)

                    critic = await self._node(store, run_id, node_name, "critic", critic_action, budget)
                    save_checkpoint(node_name, critic)

                accepted = [
                    {**originals.get(item.get("review_id") or item.get("fingerprint") or item.get("id"), {}), **item}
                    for item in critic.get("accepted", [])
                ]
                rework_requests = critic.get("rework", [])
                if not rework_requests or round_no >= 2:
                    break
                store.trace(run_id, "rework_round", "rework", round=round_no + 1, finding_count=len(rework_requests))
                for request in rework_requests:
                    review_id = request.get("review_id")
                    original = originals.get(review_id)
                    if not original:
                        continue
                    role = original.get("source_stage", "reliability")
                    rework_name = f"rework_{round_no + 1}_{review_id}"
                    if rework_name in saved_nodes:
                        updated = saved_nodes[rework_name]
                    else:
                        async def rework_action(role: str = role, original: dict[str, Any] = original, request: dict[str, Any] = request) -> dict[str, Any]:
                            self._reserve_call(budget)
                            MODEL_CALLS.labels(self.settings.model_provider, f"{role}_rework").inc()
                            BUDGET_USED.labels("model_calls").inc()
                            return await asyncio.to_thread(
                                self.provider.rework,
                                role,
                                run.diff,
                                original,
                                request.get("reason", "provide evidence"),
                                {"repository": run.repository, "evolution": evolution_context},
                            )

                        updated = await self._node(store, run_id, rework_name, role, rework_action, budget)
                        save_checkpoint(rework_name, updated)
                    if not updated.get("withdraw"):
                        originals[review_id] = {**original, **updated, "review_id": review_id}
                critic = None

            accepted = [self._restore_finding(item, originals) for item in accepted]
            patch_output = saved_nodes.get("patch_proposal") or await self._node(
                store,
                run_id,
                "patch_proposal",
                "planner",
                lambda: {"patch": self.provider.propose_patch(accepted), "rationale": "仅对支持的确定性规则生成行替换建议，其余问题保留人工修复建议。"},
                budget,
            )
            save_checkpoint("patch_proposal", patch_output)
            patch_text = patch_output.get("patch", "")
            validation = saved_nodes.get("validate") or await self._node(
                store,
                run_id,
                "validate",
                "validator",
                lambda: self.tools.call("validate_patch", "validator", patch=patch_text, allowed_files=changed_files(run.diff)),
                budget,
            )
            save_checkpoint("validate", validation)
            TOOL_CALLS.labels("validate_patch", "validator").inc()
            current = store.get_run(run_id)
            if current and current.status == "cancelled":
                raise RunCancelled(run_id)
            saved_nodes["finalize"] = {"finding_count": len(accepted)}
            run.checkpoint = {"last_node": "finalize", "nodes": saved_nodes}
            report = {
                "run_id": run_id,
                "status": "completed",
                "risk_level": self._risk_level(accepted),
                "findings": accepted,
                "patch": {"patch": patch_text, "rationale": patch_output.get("rationale", ""), "validation": validation} if patch_text else None,
                "trace_id": run_id,
                "evolution_version": active_version.version if active_version else None,
                "budget": budget,
                "checkpoint": run.checkpoint,
            }
            if not store.finish_run(run_id, owner, status="completed", risk_level=report["risk_level"], report=report, budget=budget, checkpoint=run.checkpoint):
                raise RunCancelled(run_id)
            store.trace(run_id, "run_completed", "finalize", finding_count=len(accepted), risk_level=report["risk_level"])
            RUNS.labels("completed").inc()
        except BudgetExceeded as exc:
            store.finish_run(run_id, owner, status="failed", budget=budget, report={"error": str(exc), "trace_id": run_id})
            store.trace(run_id, "run_failed", error=str(exc))
            RUNS.labels("failed").inc()
        except RunCancelled:
            store.finish_run(run_id, owner, status="cancelled", budget=budget)
            store.trace(run_id, "run_cancelled")
            RUNS.labels("cancelled").inc()
        except Exception as exc:
            store.finish_run(run_id, owner, status="failed", budget=budget, report={"error": str(exc), "trace_id": run_id})
            store.trace(run_id, "run_failed", error=f"{type(exc).__name__}: {exc}")
            RUNS.labels("failed").inc()
        finally:
            ACTIVE_RUNS.dec()
            db.close()

    async def _node(
        self,
        store: Store,
        run_id: str,
        name: str,
        role: str,
        action: Callable[[], Awaitable[dict[str, Any]] | dict[str, Any]],
        budget: dict[str, Any],
    ) -> dict[str, Any]:
        max_retries = int(budget.get("max_retries", 1))
        for _ in range(max_retries + 1):
            current = store.get_run(run_id)
            if current and current.status == "cancelled":
                raise RunCancelled(run_id)
            remaining = float(budget.get("max_seconds", 60)) - float(budget.get("used_seconds", 0.0))
            if remaining <= 0:
                raise BudgetExceeded("run time budget exceeded")
            attempt = store.next_attempt(run_id, name)
            started = time.monotonic()
            item = store.node(run_id, name, attempt, status="running")
            store.trace(run_id, "node_started", name, attempt=attempt, role=role)
            try:
                result = action()
                if asyncio.iscoroutine(result):
                    result = await asyncio.wait_for(result, timeout=min(30.0, remaining))
                duration = time.monotonic() - started
                budget["used_seconds"] = round(float(budget.get("used_seconds", 0.0)) + duration, 3)
                BUDGET_USED.labels("seconds").inc(duration)
                store.finish_node(item, "completed", result, duration_ms=duration * 1000)
                current = store.get_run(run_id)
                if current:
                    store.set_run(current, budget=budget)
                NODE_DURATION.labels(name).observe(duration)
                store.trace(run_id, "node_completed", name, attempt=attempt, duration_ms=round(duration * 1000, 2))
                return result
            except Exception as exc:
                duration = time.monotonic() - started
                budget["used_seconds"] = round(float(budget.get("used_seconds", 0.0)) + duration, 3)
                BUDGET_USED.labels("seconds").inc(duration)
                store.finish_node(item, "failed", error=str(exc), duration_ms=duration * 1000)
                current = store.get_run(run_id)
                if current:
                    store.set_run(current, budget=budget)
                store.trace(run_id, "node_failed", name, attempt=attempt, error=str(exc))
                if isinstance(exc, (BudgetExceeded, RunCancelled)):
                    raise
                if attempt >= max_retries + 1:
                    raise
                NODE_RETRIES.labels(name).inc()
        raise RuntimeError(f"node {name} exhausted retries")

    @staticmethod
    def _reserve_call(budget: dict[str, Any]) -> None:
        if budget.get("used_model_calls", 0) >= budget.get("max_model_calls", 12):
            raise BudgetExceeded("model call budget exceeded")
        budget["used_model_calls"] = budget.get("used_model_calls", 0) + 1

    @staticmethod
    def _plan(diff: str) -> dict[str, Any]:
        lowered = diff.lower()
        roles = ["security", "reliability"]
        if not any(word in lowered for word in ("password", "eval", "shell", "secret", "yaml", "verify")):
            roles = ["reliability", "security"]
        return {"roles": roles, "reason": "风险未知时并行执行安全与可靠性复核", "max_rework_rounds": 2}

    @staticmethod
    def _aggregate(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        findings: list[dict[str, Any]] = []
        for result in results.values():
            for finding in result.get("findings", []):
                fp = finding.get("fingerprint") or finding.get("id")
                if fp not in seen:
                    seen.add(fp)
                    findings.append(finding)
        return {"findings": findings, "count": len(findings)}

    @staticmethod
    def _restore_finding(item: dict[str, Any], originals: dict[str, dict[str, Any]]) -> dict[str, Any]:
        key = item.get("review_id") or item.get("fingerprint") or item.get("id")
        original = originals.get(key, {})
        result = {**original, **item}
        result.setdefault("source_stage", original.get("source_stage", "blind_critic"))
        result.pop("review_id", None)
        return result

    @staticmethod
    def _risk_level(findings: list[dict[str, Any]]) -> str:
        severities = {f.get("severity") for f in findings}
        if "critical" in severities:
            return "critical"
        if "high" in severities:
            return "high"
        if "medium" in severities:
            return "medium"
        return "low"


runtime = RuntimeManager()
