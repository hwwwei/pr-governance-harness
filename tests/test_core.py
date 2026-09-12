from app.benchmark import build_cases, evaluate
from app.github import GitHubClient
from app.providers import MockProvider
from app.tools import MCPToolAdapter, ToolDenied, ToolRegistry


def test_mock_provider_detects_security_and_is_deterministic():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,0 +1,1 @@\n+eval(value)\n"
    provider = MockProvider()
    first = provider.analyze("security", diff)
    second = provider.analyze("security", diff)
    assert first == second
    assert first["findings"][0]["type"] == "code_injection"


def test_tool_allowlist_denies_unknown_role():
    registry = ToolRegistry()
    try:
        registry.call("validate_patch", "security", patch="")
    except ToolDenied:
        pass
    else:
        raise AssertionError("tool call should be denied")


def test_mcp_adapter_requires_explicit_registration():
    adapter = MCPToolAdapter(ToolRegistry())
    try:
        adapter.invoke("demo", "lookup", "security", query="x")
    except ToolDenied:
        pass
    else:
        raise AssertionError("unregistered MCP tool should be denied")

    adapter.register("demo", "lookup", lambda query: query.upper(), {"security"})
    assert adapter.invoke("demo", "lookup", "security", query="x") == "X"


def test_benchmark_has_fixed_splits_and_metrics():
    cases = build_cases()
    result = evaluate()
    assert len(cases) == 20
    assert result["validation_cases"] + result["holdout_cases"] == 20
    assert 0 <= result["metrics"]["f1"] <= 1


def test_github_signature():
    body = b'{"action":"opened"}'
    import hashlib, hmac
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert GitHubClient.verify_signature(body, signature, "secret")
    assert not GitHubClient.verify_signature(body, "sha256=bad", "secret")
