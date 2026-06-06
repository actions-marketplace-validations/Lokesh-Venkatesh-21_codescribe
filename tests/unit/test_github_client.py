import httpx
import pytest

from app.core.config import Settings
from app.services.github import GitHubClient


class FakeAsyncClient:
    calls: list[tuple[str, dict | None, dict | None]] = []

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, headers: dict | None = None, params: dict | None = None):
        self.calls.append((url, headers, params))
        if headers and headers.get("Accept") == "application/vnd.github.v3.diff":
            return httpx.Response(200, text="diff --git a/app.py b/app.py")

        page = params["page"] if params else 1
        if page == 1:
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": f"file-{index}.py",
                        "status": "modified",
                        "patch": "@@\n+def changed():\n+    pass\n",
                        "additions": 2,
                        "deletions": 0,
                        "sha": f"sha-{index}",
                    }
                    for index in range(100)
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "filename": "last.ts",
                    "status": "added",
                    "patch": "@@\n+function changed() {}\n",
                    "additions": 1,
                    "deletions": 0,
                    "sha": "sha-last",
                }
            ],
        )


class StickyCommentClient:
    calls: list[tuple[str, str]] = []

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, headers: dict | None = None, params: dict | None = None):
        del headers, params
        self.calls.append(("get", url))
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "id": 100,
                    "url": "https://api.github.test/comment/100",
                    "body": "<!-- codescribe-agent -->\nOld body",
                    "user": {"type": "Bot"},
                }
            ],
        )

    async def patch(self, url: str, headers: dict | None = None, json: dict | None = None):
        del headers
        self.calls.append(("patch", url))
        assert json
        assert "<!-- codescribe-agent -->" in json["body"]
        assert "New body" in json["body"]
        request = httpx.Request("PATCH", url)
        return httpx.Response(200, request=request, json={"id": 100, "body": json["body"]})

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None):
        del headers, json
        self.calls.append(("post", url))
        raise AssertionError("sticky comment should edit the existing marker comment")


@pytest.mark.asyncio
async def test_github_client_paginates_files_and_fetches_diff(monkeypatch) -> None:
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.github.httpx.AsyncClient", FakeAsyncClient)

    settings = Settings(github_token="token", github_api_base_url="https://api.github.test")
    client = GitHubClient(settings)

    files = await client.list_pull_request_files("acme/widgets", 22)
    diff = await client.get_pull_request_diff("acme/widgets", 22)

    assert len(files) == 101
    assert files[0].filename == "file-0.py"
    assert files[-1].filename == "last.ts"
    assert diff == "diff --git a/app.py b/app.py"
    assert FakeAsyncClient.calls[0][2] == {"per_page": 100, "page": 1}
    assert FakeAsyncClient.calls[1][2] == {"per_page": 100, "page": 2}


@pytest.mark.asyncio
async def test_github_client_upserts_sticky_comment(monkeypatch) -> None:
    StickyCommentClient.calls = []
    monkeypatch.setattr("app.services.github.httpx.AsyncClient", StickyCommentClient)

    client = GitHubClient(
        Settings(github_token="token", github_api_base_url="https://api.github.test")
    )
    result = await client.upsert_sticky_pr_comment("acme/widgets", 22, "New body")

    assert result["mode"] == "updated"
    assert StickyCommentClient.calls == [
        ("get", "https://api.github.test/repos/acme/widgets/issues/22/comments"),
        ("patch", "https://api.github.test/comment/100"),
    ]
