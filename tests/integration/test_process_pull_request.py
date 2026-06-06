import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.github import GitHubClient


def test_process_pull_request_creates_artifacts_and_review_workflow(monkeypatch) -> None:
    published = {"reviews": 0, "summary_comments": 0}

    async def fake_review(self, repo_full_name, pr_number, body, event, comments):
        published["reviews"] += 1
        assert repo_full_name == "acme/widgets"
        assert pr_number == 7
        assert event in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}
        assert body
        assert comments
        return {"id": "mock-review-id"}

    async def fake_comment(self, repo_full_name, pr_number, body):
        published["summary_comments"] += 1
        assert repo_full_name == "acme/widgets"
        assert pr_number == 7
        assert "CodeScribe Approval Recommendation" in body
        return {"id": "mock-comment-id", "mode": "updated"}

    monkeypatch.setattr(GitHubClient, "create_pull_request_review", fake_review)
    monkeypatch.setattr(GitHubClient, "upsert_sticky_pr_comment", fake_comment)

    payload = {
        "repo_full_name": "acme/widgets",
        "pr_number": 7,
        "head_sha": "abc123",
        "title": "Add pricing",
        "author": "octocat",
        "files": [
            {
                "filename": "pricing.py",
                "status": "added",
                "patch": (
                    "@@\n+def quote(quantity):\n+    print(quantity)\n+    return quantity * 10\n"
                ),
                "additions": 3,
                "deletions": 0,
            }
        ],
    }

    with TestClient(app) as client:
        response = client.post("/api/v1/pull-requests/process", json=payload)
        assert response.status_code == 200
        body = response.json()
        risk_response = client.get(f"/api/v1/pr/{body['pull_request_id']}/risk")
        security_response = client.get(f"/api/v1/pr/{body['pull_request_id']}/security")
        quality_response = client.get(f"/api/v1/pr/{body['pull_request_id']}/quality")
        review_response = client.get(f"/api/v1/pr/{body['pull_request_id']}/review")
        review_id = review_response.json()["review_id"]
        feedback_response = client.post(
            f"/api/v1/review/{review_id}/feedback",
            json={
                "human_reviewer_decision": "NEEDS_HUMAN_REVIEW",
                "outcome": "accepted",
                "reviewer": "docs-lead",
                "team": "platform",
                "notes": "The missing-test comment is useful.",
            },
        )
        metrics_response = client.get("/api/v1/metrics")
        accuracy_response = client.get("/api/v1/metrics/accuracy")
        agreement_response = client.get("/api/v1/metrics/reviewer-agreement")
        blocked_publish = client.post(f"/api/v1/pr/{body['pull_request_id']}/publish-review")
        approve_response = client.post(f"/api/v1/pr/{body['pull_request_id']}/approve")
        publish_response = client.post(f"/api/v1/pr/{body['pull_request_id']}/publish-review")

    assert body["artifact_count"] == 10
    assert body["quality_score"] > 0
    assert risk_response.status_code == 200
    assert "risk_score" in risk_response.json()["metrics"]
    assert security_response.status_code == 200
    assert quality_response.status_code == 200
    assert "overall_quality_score" in quality_response.json()["metrics"]
    assert review_response.status_code == 200
    assert review_response.json()["comments"]
    assert feedback_response.status_code == 200
    assert feedback_response.json()["outcome"] == "accepted"
    assert metrics_response.status_code == 200
    assert metrics_response.json()["current"]["acceptance_rate"] >= 0
    assert accuracy_response.status_code == 200
    assert "false_positive_rate" in accuracy_response.json()
    assert agreement_response.status_code == 200
    assert "reviewer_agreement_rate" in agreement_response.json()
    assert blocked_publish.status_code == 409
    assert approve_response.status_code == 200
    assert approve_response.json()["publication_status"] == "approved_for_publication"
    assert publish_response.status_code == 200
    assert publish_response.json()["publication_status"] == "published"
    assert published == {"reviews": 1, "summary_comments": 1}

    dataset_path = Path("work/test_training_dataset.jsonl")
    assert dataset_path.exists()
    dataset_row = json.loads(dataset_path.read_text().strip().splitlines()[-1])
    assert dataset_row["pr_id"] == body["pull_request_id"]
    assert dataset_row["outcome"] == "accepted"


def test_process_pull_request_is_idempotent_for_same_revision() -> None:
    payload = {
        "repo_full_name": "acme/widgets",
        "pr_number": 77,
        "head_sha": "same-revision",
        "title": "Add retry policy",
        "author": "octocat",
        "files": [
            {
                "filename": "retry.py",
                "status": "added",
                "patch": "@@\n+def retry_once():\n+    return True\n",
                "additions": 2,
                "deletions": 0,
            }
        ],
    }

    with TestClient(app) as client:
        first = client.post("/api/v1/pull-requests/process", json=payload)
        second = client.post("/api/v1/pull-requests/process", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["pull_request_id"] == first.json()["pull_request_id"]
    assert second.json()["status"] == "ready_for_review"
