import hashlib
import json
import re
import time
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from .diffs import added_lines
from .schemas import Finding


class ProviderError(RuntimeError):
    pass


class AgentProvider(Protocol):
    def analyze(self, role: str, diff: str, context: dict[str, Any] | None = None) -> dict[str, Any]: ...

    def critic(self, findings: list[dict[str, Any]]) -> dict[str, Any]: ...

    def rework(self, role: str, diff: str, finding: dict[str, Any], critique: str, context: dict[str, Any] | None = None) -> dict[str, Any]: ...

    def propose_patch(self, findings: list[dict[str, Any]]) -> str: ...


class CriticResponse(BaseModel):
    decision: str = Field(pattern="^(accept|clean|reject|rework)$")
    accepted: list[dict[str, Any]] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    rework: list[dict[str, Any]] = Field(default_factory=list)


def _fingerprint(kind: str, file: str, line: int | None, evidence: str) -> str:
    normalized = re.sub(r"\s+", " ", evidence.lower())[:100]
    raw = f"{kind}:{file}:{line}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class MockProvider:
    """Deterministic provider used for offline runs and repeatable tests."""

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, role: str, diff: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls += 1
        findings: list[dict[str, Any]] = []
        for added in added_lines(diff):
            idx, current_file, text = added.line, added.file, added.text
            if role == "security":
                patterns = [
                    (r"eval\s*\(", "code_injection", "critical", "动态执行外部可控代码"),
                    (r"shell\s*=\s*True|os\.system\(", "command_injection", "high", "命令执行缺少安全边界"),
                    (r"password\s*=|api[_-]?key\s*=|secret\s*=", "secret_exposure", "high", "疑似硬编码凭据"),
                    (r"yaml\.load\(", "unsafe_deserialization", "high", "不安全反序列化调用"),
                    (r"verify\s*=\s*False", "tls_bypass", "medium", "关闭 TLS 证书校验"),
                ]
                for pattern, kind, severity, explanation in patterns:
                    if re.search(pattern, text, re.I):
                        evidence = text.strip()
                        findings.append(self._finding(kind, severity, current_file, idx, evidence, explanation, role))
            elif role == "reliability":
                patterns = [
                    (r"except\s*:|except Exception", "broad_exception", "medium", "异常被宽泛捕获，可能隐藏故障"),
                    (r"except.*pass|pass\s*#", "swallowed_exception", "high", "异常被静默吞掉"),
                    (r"requests\.(get|post)\(", "missing_timeout", "medium", "外部请求未显式设置超时"),
                    (r"ThreadPoolExecutor|asyncio\.gather", "concurrency_boundary", "medium", "并发边界需要取消和资源控制"),
                    (r"TODO|FIXME", "unfinished_path", "low", "变更留下未完成路径"),
                ]
                for pattern, kind, severity, explanation in patterns:
                    if re.search(pattern, text, re.I):
                        evidence = text.strip()
                        findings.append(self._finding(kind, severity, current_file, idx, evidence, explanation, role))
        version = (context or {}).get("evolution") or {}
        disabled = set((version.get("candidate") or {}).get("disabled_fingerprints", []))
        findings = [item for item in findings if item["fingerprint"] not in disabled]
        validated = [Finding.model_validate(item).model_dump() for item in findings]
        return {"findings": validated, "summary": f"{role} agent produced {len(validated)} findings"}

    def _finding(self, kind: str, severity: str, file: str, line: int, evidence: str, explanation: str, role: str) -> dict[str, Any]:
        return {
            "id": f"{role}-{_fingerprint(kind, file, line, evidence)}",
            "kind": "security" if role == "security" else "reliability",
            "type": kind,
            "severity": severity,
            "file": file,
            "line": line,
            "evidence": evidence,
            "explanation": explanation,
            "confidence": 0.94 if severity in {"high", "critical"} else 0.78,
            "recommendation": "限制输入并增加显式校验、超时或安全 API。",
            "source_stage": role,
            "fingerprint": _fingerprint(kind, file, line, evidence),
        }

    def critic(self, findings: list[dict[str, Any]]) -> dict[str, Any]:
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        rework: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in findings:
            fp = item.get("fingerprint") or item.get("id")
            if fp in seen:
                continue
            seen.add(fp)
            confidence = float(item.get("confidence", 0))
            if confidence < 0.55:
                rejected.append({"review_id": item.get("review_id"), "reason": "insufficient evidence"})
            elif confidence < 0.85:
                rework.append({"review_id": item.get("review_id"), "reason": "add stronger evidence or withdraw"})
            else:
                accepted.append(item)
        decision = "accept" if accepted and not rework else "rework" if rework else "clean"
        return CriticResponse(decision=decision, accepted=accepted, rejected=rejected, rework=rework).model_dump()

    def rework(self, role: str, diff: str, finding: dict[str, Any], critique: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls += 1
        updated = dict(finding)
        updated["confidence"] = max(float(updated.get("confidence", 0)), 0.9)
        updated["explanation"] = f"{updated.get('explanation', '')} Re-reviewed by {role}: {critique}."
        return Finding.model_validate(updated).model_dump()

    def propose_patch(self, findings: list[dict[str, Any]]) -> str:
        if not findings:
            return ""
        lines: list[str] = []
        seen: set[tuple[str, int]] = set()
        for item in findings:
            file = item.get("file") or "unknown"
            line_no = int(item.get("line") or 1)
            evidence = str(item.get("evidence") or "").lstrip("+ ").rstrip()
            replacement = self._safe_replacement(evidence)
            if not replacement or (file, line_no) in seen:
                continue
            seen.add((file, line_no))
            lines.extend([
                f"diff --git a/{file} b/{file}",
                f"--- a/{file}",
                f"+++ b/{file}",
                f"@@ -{line_no},1 +{line_no},1 @@",
                f"-{evidence}",
                f"+{replacement}",
            ])
        return "\n".join(lines) + "\n" if lines else ""

    @staticmethod
    def _safe_replacement(evidence: str) -> str | None:
        if "yaml.load(" in evidence:
            return evidence.replace("yaml.load(", "yaml.safe_load(", 1)
        if "verify=False" in evidence:
            return evidence.replace("verify=False", "verify=True", 1)
        if "shell=True" in evidence:
            return evidence.replace("shell=True", "shell=False", 1)
        match = re.search(r"(requests\.(?:get|post)\([^)]*)\)", evidence)
        if match and "timeout=" not in match.group(1):
            return evidence[: match.end(1)] + ", timeout=10)" + evidence[match.end() :]
        return None


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, api_key: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def _request(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ]}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = httpx.post(f"{self.base_url}/chat/completions", headers=headers, json=body, timeout=30)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content) if isinstance(content, str) else content
                if not isinstance(parsed, dict):
                    raise ProviderError("model response must be a JSON object")
                return parsed
            except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, ProviderError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.1)
        raise ProviderError(str(last_error)) from last_error

    def analyze(self, role: str, diff: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._request(
            f"You are the {role} PR reviewer. Return JSON with a findings array. Every finding must include id, kind, type, severity, file, line, evidence, explanation, confidence, recommendation, source_stage and fingerprint.",
            {"role": role, "diff": diff, "context": context or {}},
        )
        if not isinstance(result.get("findings"), list):
            raise ProviderError("model response findings must be an array")
        try:
            result["findings"] = [Finding.model_validate(item).model_dump() for item in result["findings"]]
        except ValidationError as exc:
            raise ProviderError(f"invalid finding schema: {exc}") from exc
        return result

    def critic(self, findings: list[dict[str, Any]]) -> dict[str, Any]:
        result = self._request("You are a blind critic. Return JSON with decision, accepted, rejected and rework.", {"findings": findings})
        try:
            return CriticResponse.model_validate(result).model_dump()
        except ValidationError as exc:
            raise ProviderError(f"invalid critic schema: {exc}") from exc

    def rework(self, role: str, diff: str, finding: dict[str, Any], critique: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._request(
            f"You are the {role} PR reviewer performing bounded rework. Return one complete finding or {{\"withdraw\": true}}.",
            {"diff": diff, "finding": finding, "critique": critique, "context": context or {}},
        )
        if result.get("withdraw"):
            return {"withdraw": True, "review_id": finding.get("review_id")}
        try:
            return Finding.model_validate(result).model_dump()
        except ValidationError as exc:
            raise ProviderError(f"invalid rework schema: {exc}") from exc

    def propose_patch(self, findings: list[dict[str, Any]]) -> str:
        # Patch delivery stays deterministic and read-only even when review uses a real model.
        return MockProvider().propose_patch(findings)


def build_provider(settings: Any) -> AgentProvider:
    if settings.model_provider.lower() in {"openai", "openai-compatible", "real"} and settings.model_base_url:
        return OpenAICompatibleProvider(settings.model_base_url, settings.model_name, settings.model_api_key)
    return MockProvider()
