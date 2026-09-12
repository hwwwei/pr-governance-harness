from prometheus_client import Counter, Gauge, Histogram, generate_latest

RUNS = Counter("harness_runs_total", "Runs by terminal status", ["status"])
NODE_DURATION = Histogram("harness_node_duration_seconds", "Node execution time", ["node"])
NODE_RETRIES = Counter("harness_node_retries_total", "Node retries", ["node"])
MODEL_CALLS = Counter("harness_model_calls_total", "Model calls", ["provider", "role"])
TOOL_CALLS = Counter("harness_tool_calls_total", "Tool calls", ["tool", "role"])
BUDGET_USED = Counter("harness_budget_units_total", "Consumed model-call and wall-clock budget", ["unit"])
FINDINGS = Counter("harness_findings_total", "Findings by category", ["kind", "severity"])
EVOLUTION_GATE = Counter("harness_evolution_gate_total", "Evolution gate decisions", ["decision"])
ACTIVE_RUNS = Gauge("harness_active_runs", "Active runs")


def metrics_payload() -> bytes:
    return generate_latest()
