import argparse
import asyncio
import fnmatch
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.agent_config import AgentConfig, load_agent_config, merge_agent_config
from app.core.config import Settings
from app.db.models import ReviewDecision
from app.services.ast_analysis import ASTAnalyzer
from app.services.branch_documentation import (
    apply_comment_only_annotations,
    commit_branch_documentation,
    upsert_documentation_audit,
)
from app.services.generators import DocumentationGenerator
from app.services.github import GitHubClient
from app.services.pr_intelligence import PRIntelligenceEngine
from app.services.review_agent import ReviewAgent


class RiskThresholdExceeded(RuntimeError):
    def __init__(self, risk_score: int, threshold: int) -> None:
        self.risk_score = risk_score
        self.threshold = threshold
        super().__init__(f"CodeScribe risk score {risk_score} exceeds threshold {threshold}")


@dataclass(frozen=True)
class PublishOptions:
    post_comment: bool = False
    post_review: bool = False
    auto_approve: bool = False
    fail_on_risk: bool = False
    write_artifacts: bool = True
    annotate_code: bool = False
    commit_documentation: bool = False
    documentation_file: str = "documentation.md"
    head_branch: str | None = None
    pr_author: str = ""
    pr_url: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(prog="codescribe")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser(
        "analyze-pr",
        help="Analyze a pull request from a local git diff",
    )
    analyze.add_argument("--repo", required=True)
    analyze.add_argument("--pr-number", type=int, required=True)
    analyze.add_argument("--base-ref", required=True)
    analyze.add_argument("--head-ref", required=True)
    analyze.add_argument("--output-dir", default="codescribe-reports")
    analyze.add_argument("--post-comment", default="false")
    analyze.add_argument("--post-review", default="false")
    analyze.add_argument("--auto-approve", default="false")
    analyze.add_argument("--fail-on-risk", default="false")
    analyze.add_argument("--risk-threshold", type=int)
    analyze.add_argument("--llm-provider")
    analyze.add_argument("--model")
    analyze.add_argument("--include")
    analyze.add_argument("--exclude")
    analyze.add_argument("--config-file", default=".codescribe.yml")
    analyze.add_argument("--write-artifacts", default="true")
    analyze.add_argument("--annotate-code", default="false")
    analyze.add_argument("--commit-documentation", default="false")
    analyze.add_argument("--documentation-file", default="documentation.md")
    analyze.add_argument("--head-branch")
    analyze.add_argument("--pr-author", default="")
    analyze.add_argument("--pr-url", default="")

    args = parser.parse_args()
    if args.command == "analyze-pr":
        try:
            asyncio.run(
                analyze_pr_command(
                    repo=args.repo,
                    pr_number=args.pr_number,
                    base_ref=args.base_ref,
                    head_ref=args.head_ref,
                    output_dir=Path(args.output_dir),
                    post_comment=_parse_bool(args.post_comment),
                    post_review=_parse_bool(args.post_review),
                    auto_approve=_parse_bool(args.auto_approve),
                    fail_on_risk=_parse_bool(args.fail_on_risk),
                    risk_threshold=args.risk_threshold,
                    llm_provider=args.llm_provider,
                    model=args.model,
                    include=args.include,
                    exclude=args.exclude,
                    config_file=args.config_file,
                    write_artifacts=_parse_bool(args.write_artifacts),
                    annotate_code=_parse_bool(args.annotate_code),
                    commit_documentation=_parse_bool(args.commit_documentation),
                    documentation_file=args.documentation_file,
                    head_branch=args.head_branch,
                    pr_author=args.pr_author,
                    pr_url=args.pr_url,
                )
            )
        except RiskThresholdExceeded as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc


async def analyze_pr_command(
    repo: str,
    pr_number: int,
    base_ref: str,
    head_ref: str,
    output_dir: Path,
    post_comment: bool = False,
    post_review: bool = False,
    auto_approve: bool = False,
    fail_on_risk: bool = False,
    risk_threshold: int | None = None,
    llm_provider: str | None = None,
    model: str | None = None,
    include: str | list[str] | None = None,
    exclude: str | list[str] | None = None,
    config_file: str | Path | None = ".codescribe.yml",
    write_artifacts: bool = True,
    annotate_code: bool = False,
    commit_documentation: bool = False,
    documentation_file: str = "documentation.md",
    head_branch: str | None = None,
    pr_author: str = "",
    pr_url: str = "",
) -> list[Path]:
    agent_config = merge_agent_config(
        load_agent_config(config_file),
        risk_threshold=risk_threshold,
        include=include,
        exclude=exclude,
        llm_provider=llm_provider,
        model=model,
    )
    diff = _git_diff(base_ref, head_ref)
    changed_files = filter_changed_files(changed_files_from_diff(diff), agent_config)
    if commit_documentation:
        changed_files = [
            file_data
            for file_data in changed_files
            if file_data["filename"] != documentation_file
        ]
    return await generate_reports(
        repo=repo,
        pr_number=pr_number,
        changed_files=changed_files,
        output_dir=output_dir,
        publish=PublishOptions(
            post_comment=post_comment,
            post_review=post_review,
            auto_approve=auto_approve,
            fail_on_risk=fail_on_risk,
            write_artifacts=write_artifacts,
            annotate_code=annotate_code,
            commit_documentation=commit_documentation,
            documentation_file=documentation_file,
            head_branch=head_branch,
            pr_author=pr_author,
            pr_url=pr_url,
        ),
        agent_config=agent_config,
    )


async def generate_reports(
    repo: str,
    pr_number: int,
    changed_files: list[dict[str, Any]],
    output_dir: Path,
    post_comment: bool = False,
    publish: PublishOptions | None = None,
    agent_config: AgentConfig | None = None,
    settings: Settings | None = None,
) -> list[Path]:
    publish = publish or PublishOptions(post_comment=post_comment)
    agent_config = agent_config or AgentConfig()
    settings = _settings_for_agent(settings or Settings(), agent_config)
    output_dir.mkdir(parents=True, exist_ok=True)
    analyzer = ASTAnalyzer()
    changed_files = filter_changed_files(changed_files, agent_config)
    if publish.commit_documentation:
        changed_files = [
            file_data
            for file_data in changed_files
            if file_data["filename"] != publish.documentation_file
        ]
    parsed_files = [
        analyzer.analyze_patch(file_data["filename"], file_data.get("patch"))
        for file_data in changed_files
    ]

    documentation_sections = []
    if publish.write_artifacts:
        generator = DocumentationGenerator(settings)
        for parsed_file, file_data in zip(parsed_files, changed_files, strict=False):
            for symbol in parsed_file.symbols:
                if symbol.kind == "function":
                    doc = await generator.generate_function_documentation(
                        parsed_file,
                        symbol,
                        file_data.get("patch"),
                    )
                    documentation_sections.append(doc.content)
                elif symbol.kind == "class":
                    doc = await generator.generate_class_documentation(
                        parsed_file,
                        symbol,
                        file_data.get("patch"),
                    )
                    documentation_sections.append(doc.content)
            module_doc = await generator.generate_module_summary(
                parsed_file,
                file_data.get("patch"),
            )
            documentation_sections.append(module_doc.content)

    intelligence, reports = PRIntelligenceEngine().analyze(
        repo,
        pr_number,
        changed_files,
        parsed_files,
    )
    review, review_report = ReviewAgent().review(changed_files, parsed_files, intelligence)

    written: list[Path] = []
    if publish.write_artifacts and "documentation_report.md" in agent_config.enabled_reports:
        written.append(
            _write_report(
                output_dir / "documentation_report.md",
                "# Documentation Report\n\n" + "\n\n---\n\n".join(documentation_sections),
            )
        )
        for report in reports:
            if report.title in agent_config.enabled_reports:
                written.append(_write_report(output_dir / report.title, report.content))
        if review_report.title in agent_config.enabled_reports:
            written.append(_write_report(output_dir / review_report.title, review_report.content))

    branch_paths: list[str] = []
    annotated_files: list[str] = []
    skipped_annotations: dict[str, str] = {}
    if publish.annotate_code:
        annotated_files, skipped_annotations = apply_comment_only_annotations(
            changed_files,
            parsed_files,
        )
        branch_paths.extend(annotated_files)

    if publish.commit_documentation:
        documentation_path = upsert_documentation_audit(
            repo=repo,
            pr_number=pr_number,
            pr_author=publish.pr_author,
            pr_url=publish.pr_url,
            changed_files=changed_files,
            parsed_files=parsed_files,
            risk_score=intelligence.risk.score,
            decision=review.decision,
            documentation_file=Path(publish.documentation_file),
        )
        branch_paths.append(documentation_path)
        commit_branch_documentation(
            branch_paths,
            branch=publish.head_branch,
        )

    github = GitHubClient(settings)
    if publish.post_comment:
        body = _sticky_comment_body(
            repo,
            pr_number,
            intelligence.risk.score,
            review.decision,
            written,
            changed_files,
            parsed_files,
            annotated_files,
            skipped_annotations,
        )
        await github.upsert_sticky_pr_comment(repo, pr_number, body)

    if publish.post_review:
        event = _github_review_event(review.decision, auto_approve=publish.auto_approve)
        await github.create_pull_request_review(
            repo,
            pr_number,
            _review_body(review),
            event,
            [
                {
                    "path": comment.path,
                    "line": comment.line,
                    "side": "RIGHT",
                    "body": (
                        f"**Severity:** {comment.severity}\n\n"
                        f"**Issue:** {comment.issue}\n\n"
                        f"**Suggested improvement:** {comment.suggestion}"
                    ),
                }
                for comment in review.comments
            ],
        )

    if publish.fail_on_risk and intelligence.risk.score > agent_config.risk_threshold:
        raise RiskThresholdExceeded(intelligence.risk.score, agent_config.risk_threshold)

    return written


def filter_changed_files(
    changed_files: list[dict[str, Any]],
    agent_config: AgentConfig,
) -> list[dict[str, Any]]:
    included = []
    for file_data in changed_files:
        path = file_data["filename"]
        if agent_config.include and not any(
            fnmatch.fnmatch(path, pattern) for pattern in agent_config.include
        ):
            continue
        if agent_config.exclude and any(
            fnmatch.fnmatch(path, pattern) for pattern in agent_config.exclude
        ):
            continue
        included.append(file_data)
    return included


def changed_files_from_diff(diff: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    patch_lines: list[str] = []

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if current:
                current["patch"] = "\n".join(patch_lines)
                files.append(current)
            current = _file_from_diff_header(line)
            patch_lines = [line]
            continue
        if not current:
            continue
        patch_lines.append(line)
        if line.startswith("new file mode"):
            current["status"] = "added"
        elif line.startswith("deleted file mode"):
            current["status"] = "removed"
        elif line.startswith("+") and not line.startswith("+++"):
            current["additions"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            current["deletions"] += 1

    if current:
        current["patch"] = "\n".join(patch_lines)
        files.append(current)

    return files


def _file_from_diff_header(line: str) -> dict[str, Any]:
    parts = line.split()
    filename = parts[-1][2:] if len(parts) >= 4 and parts[-1].startswith("b/") else parts[-1]
    return {
        "filename": filename,
        "status": "modified",
        "patch": "",
        "additions": 0,
        "deletions": 0,
    }


def _git_diff(base_ref: str, head_ref: str) -> str:
    range_spec = f"{base_ref}...{head_ref}"
    result = subprocess.run(
        ["git", "diff", "--find-renames", "--unified=80", range_spec],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _write_report(path: Path, content: str) -> Path:
    path.write_text(content + "\n", encoding="utf-8")
    return path


def _parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _settings_for_agent(settings: Settings, agent_config: AgentConfig) -> Settings:
    updates: dict[str, Any] = {}
    if agent_config.llm_provider:
        updates["llm_provider"] = agent_config.llm_provider
    if agent_config.model:
        updates.update(
            {
                "ollama_model": agent_config.model,
                "local_model": agent_config.model,
                "generic_llm_model": agent_config.model,
                "gemini_model": agent_config.model,
            }
        )
    return settings.model_copy(update=updates) if updates else settings


def _github_review_event(decision: ReviewDecision, *, auto_approve: bool) -> str:
    if decision == ReviewDecision.APPROVE:
        return "APPROVE" if auto_approve else "COMMENT"
    if decision == ReviewDecision.REQUEST_CHANGES:
        return "REQUEST_CHANGES"
    return "COMMENT"


def _sticky_comment_body(
    repo: str,
    pr_number: int,
    risk_score: int,
    decision: ReviewDecision,
    written: list[Path],
    changed_files: list[dict[str, Any]],
    parsed_files: list[Any],
    annotated_files: list[str],
    skipped_annotations: dict[str, str],
) -> str:
    files = "\n".join(
        f"- `{item['filename']}` (+{item.get('additions', 0)}/-{item.get('deletions', 0)})"
        for item in changed_files
    ) or "- None detected"
    functions = "\n".join(
        f"- `{parsed.path}:{symbol.name}`"
        for parsed in parsed_files
        for symbol in parsed.symbols
        if symbol.kind == "function"
    ) or "- None detected"
    code_comments = "\n".join(f"- `{path}`" for path in annotated_files) or "- None added"
    skipped = "\n".join(
        f"- `{path}`: {reason}" for path, reason in skipped_annotations.items()
    )
    reports = "\n".join(f"- `{path.name}`" for path in written)
    skipped_section = f"### Annotation skips\n{skipped}\n\n" if skipped else ""
    reports_section = f"### Generated reports\n{reports}\n" if reports else ""
    return (
        "<!-- codescribe-agent -->\n"
        "## CodeScribe PR Review\n\n"
        f"Repository: `{repo}`\n\n"
        f"PR: `#{pr_number}`\n\n"
        f"Decision: `{decision}`\n\n"
        f"Risk score: `{risk_score}/100`\n\n"
        f"### Files changed\n{files}\n\n"
        f"### Functions changed\n{functions}\n\n"
        f"### Code comments added\n{code_comments}\n\n"
        f"{skipped_section}"
        f"{reports_section}"
    )


def _review_body(review: Any) -> str:
    suggestions = "\n".join(
        f"- {suggestion}" for suggestion in review.improvement_suggestions
    ) or "- No blocking improvements identified."
    return (
        "## CodeScribe AI Review\n\n"
        f"Decision: `{review.decision}`\n\n"
        f"Confidence: `{review.confidence_score:.2f}`\n\n"
        f"Risk: {review.risk_summary}\n\n"
        f"Security: {review.security_summary}\n\n"
        f"Suggestions:\n{suggestions}"
    )


if __name__ == "__main__":
    main()
