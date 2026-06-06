from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REPORTS = {
    "documentation_report.md",
    "pr_summary.md",
    "risk_report.md",
    "security_report.md",
    "impact_analysis.md",
    "quality_report.md",
    "review_report.md",
}


@dataclass(frozen=True)
class AgentConfig:
    risk_threshold: int = 70
    include: list[str] | None = None
    exclude: list[str] | None = None
    review_tone: str = "balanced"
    review_verbosity: str = "normal"
    reports: list[str] | None = None
    llm_provider: str | None = None
    model: str | None = None

    @property
    def enabled_reports(self) -> set[str]:
        return set(self.reports or DEFAULT_REPORTS)


def load_agent_config(config_file: str | Path | None) -> AgentConfig:
    if not config_file:
        return AgentConfig()

    path = Path(config_file)
    if not path.exists():
        return AgentConfig()

    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return AgentConfig()

    return AgentConfig(
        risk_threshold=_coerce_int(loaded.get("risk_threshold"), 70),
        include=_coerce_str_list(loaded.get("include")),
        exclude=_coerce_str_list(loaded.get("exclude")),
        review_tone=str(loaded.get("review_tone") or "balanced"),
        review_verbosity=str(loaded.get("review_verbosity") or "normal"),
        reports=_normalize_reports(loaded.get("reports")),
        llm_provider=_optional_str(loaded.get("llm_provider")),
        model=_optional_str(loaded.get("model")),
    )


def merge_agent_config(
    base: AgentConfig,
    *,
    risk_threshold: int | None = None,
    include: str | list[str] | None = None,
    exclude: str | list[str] | None = None,
    llm_provider: str | None = None,
    model: str | None = None,
) -> AgentConfig:
    updates: dict[str, Any] = {}
    if risk_threshold is not None:
        updates["risk_threshold"] = risk_threshold
    if include is not None:
        updates["include"] = _coerce_str_list(include)
    if exclude is not None:
        updates["exclude"] = _coerce_str_list(exclude)
    if _optional_str(llm_provider) is not None:
        updates["llm_provider"] = _optional_str(llm_provider)
    if _optional_str(model) is not None:
        updates["model"] = _optional_str(model)
    return replace(base, **updates)


def _coerce_str_list(value: Any) -> list[str] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return None


def _normalize_reports(value: Any) -> list[str] | None:
    reports = _coerce_str_list(value)
    if reports is None:
        return None
    return [report if report.endswith(".md") else f"{report}.md" for report in reports]


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
