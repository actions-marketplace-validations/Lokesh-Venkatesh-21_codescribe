from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.db.models import (
    PullRequest,
    PullRequestReview,
    ReviewDecision,
    ReviewPublicationStatus,
)
from app.services.github import GitHubClient


class ReviewPublisher:
    def __init__(self, settings: Settings, github_client: GitHubClient | None = None) -> None:
        self.settings = settings
        self.github_client = github_client or GitHubClient(settings)

    async def publish(
        self,
        session: AsyncSession,
        pull_request_id: str,
        *,
        auto_approve: bool = False,
    ) -> PullRequestReview:
        pull_request = await session.get(PullRequest, pull_request_id)
        review = await self._latest_review(session, pull_request_id)
        if not pull_request or not review:
            raise ValueError("Pull request review not found")

        event = self._github_event(review.decision, auto_approve=auto_approve)
        body = self._summary_body(review)
        comments = [
            {
                "path": comment.path,
                "line": comment.line,
                "side": "RIGHT",
                "body": self._comment_body(comment.severity, comment.issue, comment.suggestion),
            }
            for comment in review.comments
        ]
        response = await self.github_client.create_pull_request_review(
            pull_request.repo_full_name,
            pull_request.pr_number,
            body,
            event,
            comments,
        )
        await self.github_client.upsert_sticky_pr_comment(
            pull_request.repo_full_name,
            pull_request.pr_number,
            self._recommendation_body(review),
        )

        review.github_review_id = str(response.get("id", ""))
        review.publication_status = ReviewPublicationStatus.PUBLISHED
        review.published_at = datetime.utcnow()
        for comment in review.comments:
            comment.is_published = True
        await session.commit()
        await session.refresh(review)
        return review

    async def _latest_review(
        self,
        session: AsyncSession,
        pull_request_id: str,
    ) -> PullRequestReview | None:
        stmt = (
            select(PullRequestReview)
            .where(PullRequestReview.pull_request_id == pull_request_id)
            .options(selectinload(PullRequestReview.comments))
            .order_by(PullRequestReview.created_at.desc())
        )
        return await session.scalar(stmt)

    @staticmethod
    def _github_event(decision: ReviewDecision, *, auto_approve: bool = False) -> str:
        if decision == ReviewDecision.APPROVE:
            return "APPROVE" if auto_approve else "COMMENT"
        if decision == ReviewDecision.REQUEST_CHANGES:
            return "REQUEST_CHANGES"
        return "COMMENT"

    @staticmethod
    def _summary_body(review: PullRequestReview) -> str:
        suggestions = "\n".join(f"- {suggestion}" for suggestion in review.improvement_suggestions)
        return (
            "## CodeScribe AI Review\n\n"
            f"Decision: `{review.decision}`\n\n"
            f"Confidence: `{float(review.confidence_score):.2f}`\n\n"
            f"Risk: {review.risk_summary}\n\n"
            f"Security: {review.security_summary}\n\n"
            f"Suggestions:\n{suggestions}"
        )

    @staticmethod
    def _recommendation_body(review: PullRequestReview) -> str:
        return (
            "## CodeScribe Approval Recommendation\n\n"
            f"Recommended decision: `{review.decision}`\n\n"
            f"Confidence: `{float(review.confidence_score):.2f}`\n\n"
            f"Publication status: `{review.publication_status}`"
        )

    @staticmethod
    def _comment_body(severity: str, issue: str, suggestion: str) -> str:
        return (
            f"**Severity:** {severity}\n\n"
            f"**Issue:** {issue}\n\n"
            f"**Suggested improvement:** {suggestion}"
        )
