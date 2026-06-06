from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import PullRequest
from app.db.repository import get_pull_request, upsert_pull_request_revision
from app.db.session import get_session
from app.schemas.pull_requests import (
    ProcessPullRequestRequest,
    ProcessPullRequestResponse,
    PullRequestRead,
)
from app.workflows.documentation_graph import DocumentationState, DocumentationWorkflow

router = APIRouter(prefix="/pull-requests", tags=["pull requests"])


@router.post("/process", response_model=ProcessPullRequestResponse)
async def process_pull_request(
    request: ProcessPullRequestRequest,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ProcessPullRequestResponse:
    pull_request = PullRequest(
        repo_full_name=request.repo_full_name,
        pr_number=request.pr_number,
        head_sha=request.head_sha,
        title=request.title,
        author=request.author,
        raw_payload={"source": "manual"},
    )
    pull_request = await upsert_pull_request_revision(session, pull_request)
    state = DocumentationState(pull_request=pull_request, changed_files=request.files)
    state = await DocumentationWorkflow(settings).run(session, state)
    return ProcessPullRequestResponse(
        pull_request_id=pull_request.id,
        status=pull_request.status,
        artifact_count=len(state.artifacts),
        quality_score=state.quality_score,
    )


@router.get("/{pull_request_id}", response_model=PullRequestRead)
async def read_pull_request(
    pull_request_id: str,
    session: AsyncSession = Depends(get_session),
) -> PullRequestRead:
    pull_request = await get_pull_request(session, pull_request_id)
    if not pull_request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pull request not found")
    return PullRequestRead.model_validate(pull_request)
