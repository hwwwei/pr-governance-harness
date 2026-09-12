"""Exercise one complete run through the public HTTP API using only stdlib."""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("HARNESS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DIFF = """diff --git a/src/runner.py b/src/runner.py
--- a/src/runner.py
+++ b/src/runner.py
@@ -1,0 +1,2 @@
+import subprocess
+subprocess.run(command, shell=True)
"""


def request_json(path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def main() -> None:
    created = request_json(
        "/api/v1/runs",
        "POST",
        {"repository": "acme/payment-service", "pr_number": 42, "diff": DIFF},
    )
    run_id = created["id"]
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        run = request_json(f"/api/v1/runs/{run_id}")
        if run["status"] == "completed":
            report = run["report"]
            assert report["risk_level"] in {"high", "critical"}
            assert report["findings"], "expected at least one finding"
            assert report["patch"]["validation"]["valid"] is True
            assert report["patch"]["validation"]["parseable"] is True
            assert report["patch"]["validation"]["scope_allowed"] is True
            assert report["patch"]["validation"]["safe"] is True
            print(json.dumps({"run_id": run_id, "status": run["status"], "risk": report["risk_level"], "findings": len(report["findings"])}, ensure_ascii=False))
            return
        if run["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"run terminated unexpectedly: {run}")
        time.sleep(0.5)
    raise TimeoutError(f"run {run_id} did not complete before timeout")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode()}") from exc
