import ast
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.models import ReviewDecision
from app.parsers.base import ParsedFile
from app.services.ast_analysis import ASTAnalyzer

COMMENT_MARKER = "CodeScribe:"


@dataclass(frozen=True)
class BranchDocumentationResult:
    annotated_files: list[str] = field(default_factory=list)
    skipped_files: dict[str, str] = field(default_factory=dict)
    documentation_file: str | None = None
    committed: bool = False
    commit_sha: str | None = None


def apply_comment_only_annotations(
    changed_files: list[dict[str, Any]],
    parsed_files: list[ParsedFile],
    repo_root: Path = Path("."),
) -> tuple[list[str], dict[str, str]]:
    analyzer = ASTAnalyzer()
    patch_symbols = {
        parsed.path: {(symbol.name, symbol.kind) for symbol in parsed.symbols}
        for parsed in parsed_files
    }
    annotated: list[str] = []
    skipped: dict[str, str] = {}

    for file_data in changed_files:
        path = file_data["filename"]
        if not path.endswith(".py"):
            skipped[path] = "comment-only AST validation is currently enabled for Python files"
            continue

        source_path = repo_root / path
        if not source_path.exists():
            skipped[path] = "file does not exist in checkout"
            continue

        original = source_path.read_text(encoding="utf-8")
        original_parse = _parse_python_ast(original)
        if original_parse is None:
            skipped[path] = "source does not parse before annotation"
            continue

        current_symbols = analyzer.analyze(path, original).symbols
        changed_symbol_keys = patch_symbols.get(path, set())
        targets = [
            symbol
            for symbol in current_symbols
            if (symbol.name, symbol.kind) in changed_symbol_keys
            and symbol.kind in {"function", "class"}
        ]
        if not targets:
            skipped[path] = "no changed Python functions or classes detected"
            continue

        updated = _insert_python_symbol_comments(original, targets)
        if updated == original:
            skipped[path] = "comments already present"
            continue
        if not _python_ast_equal(original, updated):
            skipped[path] = "AST changed after annotation; refusing to write"
            continue
        if _remove_codescribe_comments(updated) != original.rstrip("\n"):
            skipped[path] = "non-comment content changed; refusing to write"
            continue

        source_path.write_text(updated + "\n", encoding="utf-8")
        annotated.append(path)

    return annotated, skipped


def upsert_documentation_audit(
    *,
    repo: str,
    pr_number: int,
    pr_author: str,
    pr_url: str,
    changed_files: list[dict[str, Any]],
    parsed_files: list[ParsedFile],
    risk_score: int,
    decision: ReviewDecision,
    documentation_file: Path,
) -> str:
    marker = _pr_marker(pr_number)
    existing = documentation_file.read_text(encoding="utf-8") if documentation_file.exists() else ""
    reviewed_at = _existing_reviewed_at(existing, marker)
    section = _audit_section(
        repo=repo,
        pr_number=pr_number,
        pr_author=pr_author,
        pr_url=pr_url,
        changed_files=changed_files,
        parsed_files=parsed_files,
        risk_score=risk_score,
        decision=decision,
        reviewed_at=reviewed_at,
    )
    if marker in existing:
        pattern = re.compile(
            rf"{re.escape(marker)}.*?(?=\n<!-- codescribe-pr-\d+ -->|\Z)",
            re.DOTALL,
        )
        updated = pattern.sub(section.rstrip(), existing).rstrip() + "\n"
    else:
        prefix = existing.rstrip() + "\n\n" if existing.strip() else "# CodeScribe PR Audit Log\n\n"
        updated = prefix + section

    documentation_file.write_text(updated, encoding="utf-8")
    return str(documentation_file)


def commit_branch_documentation(
    paths: list[str],
    *,
    branch: str | None,
    message: str = "Add CodeScribe PR documentation",
) -> tuple[bool, str | None]:
    if not branch or not paths:
        return False, None

    changed = _git(["status", "--porcelain", "--", *paths])
    if not changed.strip():
        return False, None

    _git(["config", "user.name", "codescribe-agent"])
    _git(["config", "user.email", "codescribe-agent@users.noreply.github.com"])
    _git(["add", *paths])
    _git(["commit", "-m", message])
    sha = _git(["rev-parse", "HEAD"]).strip()
    _git(["push", "origin", f"HEAD:{branch}"])
    return True, sha


def _insert_python_symbol_comments(source: str, symbols: list[Any]) -> str:
    lines = source.splitlines()
    for symbol in sorted(symbols, key=lambda item: item.line, reverse=True):
        index = max(symbol.line - 1, 0)
        indent = re.match(r"\s*", lines[index]).group(0) if index < len(lines) else ""
        if index > 0 and COMMENT_MARKER in lines[index - 1]:
            continue
        label = "function" if symbol.kind == "function" else "class"
        comment = (
            f"{indent}# {COMMENT_MARKER} changed {label} `{symbol.name}` "
            "reviewed for PR context."
        )
        lines.insert(index, comment)
    return "\n".join(lines).rstrip("\n")


def _audit_section(
    *,
    repo: str,
    pr_number: int,
    pr_author: str,
    pr_url: str,
    changed_files: list[dict[str, Any]],
    parsed_files: list[ParsedFile],
    risk_score: int,
    decision: ReviewDecision,
    reviewed_at: str | None = None,
) -> str:
    now = reviewed_at or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    files = "\n".join(
        f"- `{item['filename']}` ({item.get('status', 'modified')}, "
        f"+{item.get('additions', 0)}/-{item.get('deletions', 0)})"
        for item in changed_files
    ) or "- None detected"
    functions = _changed_symbols_markdown(parsed_files, "function")
    classes = _changed_symbols_markdown(parsed_files, "class")

    return (
        f"{_pr_marker(pr_number)}\n"
        f"## PR #{pr_number} CodeScribe Audit\n\n"
        f"- Repository: `{repo}`\n"
        f"- PR: [{pr_url or f'#{pr_number}'}]({pr_url})\n"
        f"- Raised by: `{pr_author or 'unknown'}`\n"
        f"- Reviewed at: `{now}`\n"
        f"- Risk score: `{risk_score}/100`\n"
        f"- Review decision: `{decision}`\n\n"
        f"### Files Changed\n{files}\n\n"
        f"### Functions Changed\n{functions}\n\n"
        f"### Classes Changed\n{classes}\n"
    )


def _changed_symbols_markdown(parsed_files: list[ParsedFile], kind: str) -> str:
    rows = [
        f"- `{parsed.path}:{symbol.name}`"
        for parsed in parsed_files
        for symbol in parsed.symbols
        if symbol.kind == kind
    ]
    return "\n".join(rows) or "- None detected"


def _pr_marker(pr_number: int) -> str:
    return f"<!-- codescribe-pr-{pr_number} -->"


def _existing_reviewed_at(existing: str, marker: str) -> str | None:
    marker_index = existing.find(marker)
    if marker_index < 0:
        return None
    match = re.search(r"- Reviewed at: `([^`]+)`", existing[marker_index:])
    return match.group(1) if match else None


def _parse_python_ast(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _python_ast_equal(before: str, after: str) -> bool:
    before_tree = _parse_python_ast(before)
    after_tree = _parse_python_ast(after)
    if before_tree is None or after_tree is None:
        return False
    return ast.dump(before_tree, include_attributes=False) == ast.dump(
        after_tree,
        include_attributes=False,
    )


def _remove_codescribe_comments(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if COMMENT_MARKER not in line
    ).rstrip("\n")


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
