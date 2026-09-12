from typing import Any, Callable

from .diffs import validate_candidate_patch
from .schemas import PatchValidation


class ToolDenied(PermissionError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[Callable[..., Any], set[str]]] = {}
        self.register("read_diff", lambda diff: diff, {"security", "reliability", "planner", "critic"})
        self.register("validate_patch", self.validate_patch, {"validator", "planner"})

    def register(self, name: str, func: Callable[..., Any], roles: set[str]) -> None:
        self._tools[name] = (func, roles)

    def call(self, name: str, role: str, **kwargs: Any) -> Any:
        entry = self._tools.get(name)
        if not entry or role not in entry[1]:
            raise ToolDenied(f"tool '{name}' is not allowed for role '{role}'")
        return entry[0](**kwargs)

    @staticmethod
    def validate_patch(patch: str, allowed_files: list[str] | None = None) -> dict[str, Any]:
        result = validate_candidate_patch(patch, set(allowed_files or []))
        result["valid"] = bool(result["parseable"] and result["scope_allowed"] and result["safe"])
        return PatchValidation.model_validate(result).model_dump()

    def registered(self) -> list[str]:
        return sorted(self._tools)


class MCPToolAdapter:
    """Optional boundary for MCP servers; remote tools are never implicit."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self._remote: dict[tuple[str, str], tuple[Callable[..., Any], set[str]]] = {}

    def register(self, server: str, tool: str, handler: Callable[..., Any], roles: set[str]) -> None:
        self._remote[(server, tool)] = (handler, roles)

    def invoke(self, server: str, tool: str, role: str, **kwargs: Any) -> Any:
        entry = self._remote.get((server, tool))
        if not entry or role not in entry[1]:
            raise ToolDenied(f"MCP tool {server}:{tool} is not explicitly registered")
        return entry[0](**kwargs)
