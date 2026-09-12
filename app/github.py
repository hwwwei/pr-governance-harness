import hashlib
import hmac
from typing import Any

import httpx


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    @staticmethod
    def verify_signature(body: bytes, signature: str | None, secret: str | None) -> bool:
        if not secret:
            return True
        if not signature or not signature.startswith("sha256="):
            return False
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature.removeprefix("sha256="), digest)

    def fetch_pull_request(self, repository: str, number: int) -> dict[str, Any]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"https://api.github.com/repos/{repository}/pulls/{number}"
        try:
            response = httpx.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
            patch_response = httpx.get(f"{url}.patch", headers={**headers, "Accept": "application/vnd.github.patch"}, timeout=20)
            patch_response.raise_for_status()
            return {"repository": repository, "pr_number": number, "title": data.get("title"), "diff": patch_response.text}
        except httpx.HTTPError as exc:
            raise GitHubError(str(exc)) from exc
