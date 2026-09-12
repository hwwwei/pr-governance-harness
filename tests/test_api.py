from fastapi.testclient import TestClient
import time

from app.main import app


def test_api_rejects_non_unified_diff_and_unknown_run():
    with TestClient(app) as client:
        response = client.post("/api/v1/runs", json={"repository": "demo/repo", "diff": "not a diff"})
        assert response.status_code == 422
        response = client.get("/api/v1/runs/does-not-exist")
        assert response.status_code == 404


def test_api_dashboard_and_health_are_available():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").json()["status"] == "ok"


def test_api_completes_a_mock_run_with_validated_patch():
    diff = """diff --git a/src/runner.py b/src/runner.py
--- a/src/runner.py
+++ b/src/runner.py
@@ -1,0 +1,2 @@
+import subprocess
+subprocess.run(command, shell=True)
"""
    with TestClient(app) as client:
        created = client.post("/api/v1/runs", json={"repository": "demo/api", "diff": diff})
        assert created.status_code == 202
        run_id = created.json()["id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            detail = client.get(f"/api/v1/runs/{run_id}").json()
            if detail["status"] == "completed":
                assert detail["report"]["findings"]
                assert detail["report"]["patch"]["validation"]["valid"] is True
                return
            assert detail["status"] not in {"failed", "cancelled"}, detail
            time.sleep(0.1)
        raise AssertionError(f"run did not complete: {detail}")
