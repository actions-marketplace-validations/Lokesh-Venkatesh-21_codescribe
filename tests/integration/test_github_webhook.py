from fastapi.testclient import TestClient

from app.main import app
from app.services.github import GitHubChangedFile, GitHubClient


def test_github_opened_webhook_fetches_files_and_generates_artifacts(monkeypatch) -> None:
    async def fake_files(self, repo_full_name: str, pr_number: int) -> list[GitHubChangedFile]:
        assert repo_full_name == "acme/widgets"
        assert pr_number == 11
        return [
            GitHubChangedFile(
                filename="service.py",
                status="added",
                patch="@@\n+class WidgetService:\n+    def create(self):\n+        return None\n",
                additions=3,
                deletions=0,
                sha="file-sha",
                previous_filename=None,
                blob_url="https://github.com/acme/widgets/blob/head/service.py",
                raw_url="https://raw.githubusercontent.com/acme/widgets/head/service.py",
                contents_url="https://api.github.com/repos/acme/widgets/contents/service.py",
                raw={"filename": "service.py"},
            )
        ]

    async def fake_diff(self, repo_full_name: str, pr_number: int) -> str:
        assert repo_full_name == "acme/widgets"
        assert pr_number == 11
        return "diff --git a/service.py b/service.py"

    monkeypatch.setattr(GitHubClient, "list_pull_request_files", fake_files)
    monkeypatch.setattr(GitHubClient, "get_pull_request_diff", fake_diff)

    payload = {
        "action": "opened",
        "repository": {"full_name": "acme/widgets"},
        "pull_request": {
            "number": 11,
            "title": "Add widget service",
            "user": {"login": "octocat"},
            "head": {"sha": "head-sha-opened"},
        },
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/github",
            headers={"X-GitHub-Event": "pull_request"},
            json=payload,
        )
        assert response.status_code == 202
        pull_request_id = response.json()["pull_request_id"]

        read_response = client.get(f"/api/v1/pull-requests/{pull_request_id}")

    assert read_response.status_code == 200
    body = read_response.json()
    assert body["status"] == "ready_for_review"
    assert len(body["artifacts"]) == 11


def test_github_webhook_rejects_invalid_json() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/github",
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
            },
            content=b"{invalid-json",
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid GitHub webhook payload"
