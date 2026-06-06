from typing import Any

from pydantic import BaseModel, Field

from app.db.models import PullRequestStatus
from app.schemas.artifacts import ArtifactRead


class PullRequestRead(BaseModel):
    id: str
    repo_full_name: str
    pr_number: int
    head_sha: str
    title: str
    author: str
    status: PullRequestStatus
    artifacts: list[ArtifactRead] = []

    model_config = {"from_attributes": True}


class ProcessPullRequestRequest(BaseModel):
    repo_full_name: str = Field(min_length=3, max_length=255)
    pr_number: int = Field(gt=0)
    head_sha: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(min_length=1, max_length=255)
    files: list[dict[str, Any]] = Field(default_factory=list)


class ProcessPullRequestResponse(BaseModel):
    pull_request_id: str
    status: PullRequestStatus
    artifact_count: int
    quality_score: float
