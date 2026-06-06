import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitHubChangedFile:
    filename: str
    status: str
    patch: str | None
    additions: int
    deletions: int
    sha: str | None
    previous_filename: str | None
    blob_url: str | None
    raw_url: str | None
    contents_url: str | None
    raw: dict[str, Any]


class GitHubClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.github_api_base_url.rstrip("/")
        self._installation_tokens: dict[str, str] = {}

    async def _headers(
        self,
        repo_full_name: str | None = None,
        accept: str = "application/vnd.github+json",
    ) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = await self._auth_token(repo_full_name)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _auth_token(self, repo_full_name: str | None) -> str | None:
        if self.settings.github_app_id and self.settings.github_app_private_key and repo_full_name:
            return await self._installation_token(repo_full_name)
        return self.settings.github_token

    async def _installation_token(self, repo_full_name: str) -> str:
        try:
            import jwt
        except ImportError as exc:
            raise ExternalServiceError(
                "PyJWT[crypto] is required for GitHub App authentication"
            ) from exc

        if repo_full_name in self._installation_tokens:
            return self._installation_tokens[repo_full_name]

        now = int(time.time())
        app_jwt = jwt.encode(
            {
                "iat": now - 60,
                "exp": now + 540,
                "iss": self.settings.github_app_id,
            },
            self.settings.github_app_private_key,
            algorithm="RS256",
        )
        app_headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {app_jwt}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            installation = await client.get(
                f"{self.base_url}/repos/{repo_full_name}/installation",
                headers=app_headers,
            )
            installation.raise_for_status()
            installation_id = installation.json()["id"]
            token_response = await client.post(
                f"{self.base_url}/app/installations/{installation_id}/access_tokens",
                headers=app_headers,
            )
            token_response.raise_for_status()

        token = token_response.json()["token"]
        self._installation_tokens[repo_full_name] = token
        return token

    @retry(wait=wait_exponential(multiplier=0.5, min=0.5, max=4), stop=stop_after_attempt(3))
    async def list_pull_request_files(
        self, repo_full_name: str, pr_number: int
    ) -> list[GitHubChangedFile]:
        if not await self._auth_token(repo_full_name):
            logger.info("GITHUB_TOKEN missing; returning no remote files")
            return []

        files: list[GitHubChangedFile] = []
        async with httpx.AsyncClient(timeout=20) as client:
            page = 1
            while True:
                url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}/files"
                response = await client.get(
                    url,
                    headers=await self._headers(repo_full_name),
                    params={"per_page": 100, "page": page},
                )

                if response.status_code >= 400:
                    raise ExternalServiceError(
                        "GitHub files API failed with "
                        f"{response.status_code}: {response.text[:300]}"
                    )

                items = response.json()
                files.extend(self._to_changed_file(item) for item in items)
                if len(items) < 100:
                    break
                page += 1

        return files

    @retry(wait=wait_exponential(multiplier=0.5, min=0.5, max=4), stop=stop_after_attempt(3))
    async def get_pull_request_diff(self, repo_full_name: str, pr_number: int) -> str:
        if not await self._auth_token(repo_full_name):
            logger.info("GITHUB_TOKEN missing; returning empty remote diff")
            return ""

        url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                url,
                headers=await self._headers(repo_full_name, "application/vnd.github.v3.diff"),
            )

        if response.status_code >= 400:
            raise ExternalServiceError(
                f"GitHub diff API failed with {response.status_code}: {response.text[:300]}"
            )

        return response.text

    async def create_pr_comment(self, repo_full_name: str, pr_number: int, body: str) -> None:
        if not await self._auth_token(repo_full_name):
            logger.info("Dry-run GitHub comment for %s #%s:\n%s", repo_full_name, pr_number, body)
            return

        url = f"{self.base_url}/repos/{repo_full_name}/issues/{pr_number}/comments"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers=await self._headers(repo_full_name),
                json={"body": body},
            )

        if response.status_code >= 400:
            raise ExternalServiceError(
                f"GitHub comment API failed with {response.status_code}: {response.text[:300]}"
            )

    async def upsert_sticky_pr_comment(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        marker: str = "<!-- codescribe-agent -->",
    ) -> dict[str, Any]:
        body_with_marker = body if marker in body else f"{marker}\n{body}"
        if not await self._auth_token(repo_full_name):
            logger.info(
                "Dry-run sticky GitHub comment for %s #%s:\n%s",
                repo_full_name,
                pr_number,
                body_with_marker,
            )
            return {"id": "dry-run", "body": body_with_marker, "mode": "dry-run"}

        async with httpx.AsyncClient(timeout=20) as client:
            comments = await client.get(
                f"{self.base_url}/repos/{repo_full_name}/issues/{pr_number}/comments",
                headers=await self._headers(repo_full_name),
                params={"per_page": 100},
            )
            comments.raise_for_status()
            existing = next(
                (
                    item
                    for item in comments.json()
                    if marker in str(item.get("body", ""))
                    and str(item.get("user", {}).get("type", "")).lower() == "bot"
                ),
                None,
            )
            if existing:
                response = await client.patch(
                    existing["url"],
                    headers=await self._headers(repo_full_name),
                    json={"body": body_with_marker},
                )
                mode = "updated"
            else:
                response = await client.post(
                    f"{self.base_url}/repos/{repo_full_name}/issues/{pr_number}/comments",
                    headers=await self._headers(repo_full_name),
                    json={"body": body_with_marker},
                )
                mode = "created"
            response.raise_for_status()

        payload = response.json()
        payload["mode"] = mode
        return payload

    async def create_pull_request_review(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        event: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not await self._auth_token(repo_full_name):
            logger.info(
                "Dry-run GitHub review for %s #%s event=%s comments=%s:\n%s",
                repo_full_name,
                pr_number,
                event,
                len(comments),
                body,
            )
            return {"id": "dry-run", "state": event, "comments": comments}

        url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        payload = {
            "body": body,
            "event": event,
            "comments": comments,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers=await self._headers(repo_full_name),
                json=payload,
            )

        if response.status_code >= 400:
            raise ExternalServiceError(
                f"GitHub review API failed with {response.status_code}: {response.text[:300]}"
            )

        return response.json()

    @staticmethod
    def _to_changed_file(item: dict[str, Any]) -> GitHubChangedFile:
        return GitHubChangedFile(
            filename=item["filename"],
            status=item.get("status", "modified"),
            patch=item.get("patch"),
            additions=item.get("additions", 0),
            deletions=item.get("deletions", 0),
            sha=item.get("sha"),
            previous_filename=item.get("previous_filename"),
            blob_url=item.get("blob_url"),
            raw_url=item.get("raw_url"),
            contents_url=item.get("contents_url"),
            raw=item,
        )
