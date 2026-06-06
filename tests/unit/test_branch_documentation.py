from pathlib import Path

from app.db.models import ReviewDecision
from app.parsers.base import ParsedFile, SupportedLanguage, Symbol
from app.services.branch_documentation import (
    apply_comment_only_annotations,
    upsert_documentation_audit,
)


def test_apply_comment_only_annotations_preserves_python_code(tmp_path, monkeypatch) -> None:
    source_path = tmp_path / "service.py"
    source_path.write_text(
        "def changed(value):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    annotated, skipped = apply_comment_only_annotations(
        [
            {
                "filename": "service.py",
                "patch": "@@\n+def changed(value):\n+    return value + 1\n",
            }
        ],
        [
            ParsedFile(
                path="service.py",
                language=SupportedLanguage.PYTHON,
                symbols=[Symbol(name="changed", kind="function", line=1)],
            )
        ],
        repo_root=Path("."),
    )

    updated = source_path.read_text(encoding="utf-8")
    assert annotated == ["service.py"]
    assert skipped == {}
    assert "# CodeScribe: changed function `changed` reviewed for PR context." in updated
    assert "def changed(value):\n    return value + 1\n" in updated


def test_upsert_documentation_audit_records_pr_details(tmp_path) -> None:
    documentation = tmp_path / "documentation.md"

    path = upsert_documentation_audit(
        repo="acme/widgets",
        pr_number=42,
        pr_author="octocat",
        pr_url="https://github.com/acme/widgets/pull/42",
        changed_files=[
            {
                "filename": "service.py",
                "status": "modified",
                "additions": 2,
                "deletions": 0,
            }
        ],
        parsed_files=[
            ParsedFile(
                path="service.py",
                language=SupportedLanguage.PYTHON,
                symbols=[Symbol(name="changed", kind="function", line=1)],
            )
        ],
        risk_score=12,
        decision=ReviewDecision.NEEDS_HUMAN_REVIEW,
        documentation_file=documentation,
    )

    content = documentation.read_text(encoding="utf-8")
    assert path == str(documentation)
    assert "PR #42 CodeScribe Audit" in content
    assert "Raised by: `octocat`" in content
    assert "`service.py`" in content
    assert "`service.py:changed`" in content
