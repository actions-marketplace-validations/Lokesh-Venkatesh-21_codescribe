import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import verify_github_signature
from app.db.models import PullRequest
from app.db.repository import upsert_pull_request_revision
from app.db.session import AsyncSessionLocal, get_session
from app.schemas.github import PullRequestWebhook, WebhookAccepted
from app.services.github import GitHubClient
from app.workflows.documentation_graph import DocumentationState, DocumentationWorkflow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github", response_model=WebhookAccepted, status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> WebhookAccepted:
    payload_bytes = await request.body()
    if not settings.is_local and not verify_github_signature(
        settings.github_webhook_secret, payload_bytes, x_hub_signature_256
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    if x_github_event != "pull_request":
        return WebhookAccepted(pull_request_id="", status="ignored")

    try:
        payload = json.loads(payload_bytes)
        webhook = PullRequestWebhook.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Rejected invalid GitHub webhook payload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub webhook payload",
        ) from exc
    if webhook.action not in {"opened", "synchronize", "reopened", "ready_for_review"}:
        return WebhookAccepted(pull_request_id="", status="ignored")

    head_sha = webhook.pull_request.head.get("sha", "unknown")
    pull_request = PullRequest(
        repo_full_name=webhook.repository.full_name,
        pr_number=webhook.pull_request.number,
        head_sha=head_sha,
        title=webhook.pull_request.title,
        author=webhook.pull_request.user.login,
        raw_payload=payload,
    )
    pull_request = await upsert_pull_request_revision(session, pull_request)
    background_tasks.add_task(_process_pull_request_from_github, pull_request.id)
    return WebhookAccepted(pull_request_id=pull_request.id, status="accepted")


async def _process_pull_request_from_github(pull_request_id: str) -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        pull_request = await session.get(PullRequest, pull_request_id)
        if not pull_request:
            logger.error("Pull request disappeared before processing: %s", pull_request_id)
            return
        github = GitHubClient(settings)
        files = await github.list_pull_request_files(
            pull_request.repo_full_name,
            pull_request.pr_number,
        )
        diff = await github.get_pull_request_diff(
            pull_request.repo_full_name,
            pull_request.pr_number,
        )
        pull_request.raw_payload = {
            **(pull_request.raw_payload or {}),
            "github_diff": diff,
            "github_file_count": len(files),
        }
        state = DocumentationState(
            pull_request=pull_request,
            changed_files=[
                {
                    "filename": file.filename,
                    "status": file.status,
                    "patch": file.patch,
                    "additions": file.additions,
                    "deletions": file.deletions,
                    "sha": file.sha,
                    "previous_filename": file.previous_filename,
                    "blob_url": file.blob_url,
                    "raw_url": file.raw_url,
                    "contents_url": file.contents_url,
                    "raw": file.raw,
                }
                for file in files
            ],
        )
        await DocumentationWorkflow(settings).run(session, state)
