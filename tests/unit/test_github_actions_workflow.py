from pathlib import Path

import yaml


def test_codescribe_workflow_yaml_loads() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/codescribe.yml").read_text())
    triggers = workflow.get("on") or workflow.get(True)

    assert workflow["name"] == "CodeScribe PR Analysis"
    assert "pull_request" in triggers
    assert workflow["permissions"]["pull-requests"] == "write"


def test_reusable_action_yaml_loads() -> None:
    action = yaml.safe_load(Path("action.yml").read_text())

    assert action["name"] == "CodeScribe PR Intelligence"
    assert action["runs"]["using"] == "composite"
    assert action["inputs"]["post-review"]["default"] == "false"
    assert action["inputs"]["llm-provider"]["default"] == "auto"
    assert action["inputs"]["write-artifacts"]["default"] == "false"
    assert action["inputs"]["annotate-code"]["default"] == "true"
    assert action["inputs"]["commit-documentation"]["default"] == "true"
    run_step = action["runs"]["steps"][-1]
    assert run_step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert "--fail-on-risk" in run_step["run"]
    assert "--commit-documentation" in run_step["run"]


def test_container_action_yaml_loads() -> None:
    action = yaml.safe_load(Path("action-container.yml").read_text())

    assert action["runs"]["using"] == "docker"
    assert action["runs"]["image"].endswith(":v1")
    assert action["inputs"]["config-file"]["default"] == ".codescribe.yml"
