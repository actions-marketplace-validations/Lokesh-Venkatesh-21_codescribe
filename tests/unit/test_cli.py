import pytest

from app.cli import (
    PublishOptions,
    RiskThresholdExceeded,
    changed_files_from_diff,
    filter_changed_files,
    generate_reports,
)
from app.core.agent_config import load_agent_config, merge_agent_config
from app.core.config import Settings


def test_changed_files_from_diff_parses_patch_metadata() -> None:
    diff = """diff --git a/app/service.py b/app/service.py
index 111..222 100644
--- a/app/service.py
+++ b/app/service.py
@@ -1,2 +1,3 @@
+def quote(quantity):
+    return quantity * 10
"""

    files = changed_files_from_diff(diff)

    assert files == [
        {
            "filename": "app/service.py",
            "status": "modified",
            "patch": diff.rstrip("\n"),
            "additions": 2,
            "deletions": 0,
        }
    ]


@pytest.mark.asyncio
async def test_generate_reports_without_webhook_server(tmp_path) -> None:
    changed_files = [
        {
            "filename": "app/service.py",
            "status": "modified",
            "patch": "@@\n+def quote(quantity):\n+    return quantity * 10\n",
            "additions": 2,
            "deletions": 0,
        }
    ]

    written = await generate_reports(
        repo="acme/widgets",
        pr_number=1,
        changed_files=changed_files,
        output_dir=tmp_path,
        settings=Settings(llm_provider="local_fallback"),
    )

    names = {path.name for path in written}
    assert {
        "documentation_report.md",
        "risk_report.md",
        "security_report.md",
        "review_report.md",
    }.issubset(names)


def test_github_actions_mode_config() -> None:
    settings = Settings(codescribe_mode="github_action", post_pr_comment=False)

    assert settings.codescribe_mode == "github_action"
    assert not settings.post_pr_comment


def test_config_file_precedence_and_path_filtering(tmp_path) -> None:
    config_file = tmp_path / ".codescribe.yml"
    config_file.write_text(
        """
risk_threshold: 50
include:
  - "app/**"
exclude:
  - "app/generated/**"
llm_provider: local_fallback
model: config-model
""",
        encoding="utf-8",
    )

    config = merge_agent_config(
        load_agent_config(config_file),
        risk_threshold=80,
        model="flag-model",
    )
    filtered = filter_changed_files(
        [
            {"filename": "app/service.py"},
            {"filename": "app/generated/client.py"},
            {"filename": "docs/readme.md"},
        ],
        config,
    )

    assert config.risk_threshold == 80
    assert config.llm_provider == "local_fallback"
    assert config.model == "flag-model"
    assert [file_data["filename"] for file_data in filtered] == ["app/service.py"]


@pytest.mark.asyncio
async def test_generate_reports_fail_on_risk_exit_code(tmp_path) -> None:
    changed_files = [
        {
            "filename": "app/auth/service.py",
            "status": "modified",
            "patch": "@@\n+API_KEY = \"super-secret-token\"\n",
            "additions": 200,
            "deletions": 0,
        }
    ]

    with pytest.raises(RiskThresholdExceeded) as exc:
        await generate_reports(
            repo="acme/widgets",
            pr_number=99,
            changed_files=changed_files,
            output_dir=tmp_path,
            settings=Settings(llm_provider="local_fallback"),
            publish=PublishOptions(fail_on_risk=True),
            agent_config=merge_agent_config(load_agent_config(None), risk_threshold=10),
        )

    assert exc.value.risk_score > exc.value.threshold
