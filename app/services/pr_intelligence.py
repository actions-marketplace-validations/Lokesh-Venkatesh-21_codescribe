import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.parsers.base import ParsedFile
from app.services.generators import GeneratedDocument


class ChangeType(StrEnum):
    FEATURE = "Feature"
    BUG_FIX = "Bug Fix"
    REFACTOR = "Refactor"
    DOCUMENTATION = "Documentation"
    SECURITY = "Security"
    INFRASTRUCTURE = "Infrastructure"
    DEPENDENCY_UPDATE = "Dependency Update"
    TEST_CHANGES = "Test Changes"


@dataclass(frozen=True)
class SecurityFinding:
    rule: str
    severity: str
    path: str
    line: int | None
    detail: str


@dataclass(frozen=True)
class DependencyGraph:
    modified_functions: list[str]
    impacted_classes: list[str]
    edges: dict[str, list[str]]
    downstream_risks: list[str]


@dataclass(frozen=True)
class RiskAssessment:
    score: int
    drivers: list[str]
    critical_files: list[str]


@dataclass(frozen=True)
class QualityScores:
    documentation_score: int
    complexity_score: int
    maintainability_score: int
    security_score: int
    overall_quality_score: int


@dataclass
class PRIntelligenceResult:
    classifications: list[ChangeType]
    risk: RiskAssessment
    dependency_graph: DependencyGraph
    security_findings: list[SecurityFinding]
    quality: QualityScores
    business_impact: dict[str, Any]
    metrics: dict[str, float] = field(default_factory=dict)


class PRIntelligenceEngine:
    def analyze(
        self,
        repo_full_name: str,
        pr_number: int,
        changed_files: list[dict[str, Any]],
        parsed_files: list[ParsedFile],
    ) -> tuple[PRIntelligenceResult, list[GeneratedDocument]]:
        result = PRIntelligenceResult(
            classifications=self._classify(changed_files),
            risk=self._score_risk(changed_files),
            dependency_graph=self._dependency_graph(parsed_files),
            security_findings=self._security_findings(changed_files),
            quality=self._quality_scores(changed_files, parsed_files),
            business_impact={},
        )
        result.business_impact = self._business_impact(repo_full_name, pr_number, result)
        result.metrics = {
            "risk_score": result.risk.score,
            "documentation_score": result.quality.documentation_score,
            "complexity_score": result.quality.complexity_score,
            "maintainability_score": result.quality.maintainability_score,
            "security_score": result.quality.security_score,
            "overall_quality_score": result.quality.overall_quality_score,
        }
        return result, self._reports(repo_full_name, pr_number, result)

    def _classify(self, changed_files: list[dict[str, Any]]) -> list[ChangeType]:
        paths = [file_data["filename"].lower() for file_data in changed_files]
        patches = "\n".join(file_data.get("patch") or "" for file_data in changed_files).lower()
        classifications: set[ChangeType] = set()

        if any(path.endswith((".md", ".rst", ".txt")) or "/docs/" in path for path in paths):
            classifications.add(ChangeType.DOCUMENTATION)
        if any(
            "/test" in path or path.endswith(("_test.go", ".spec.ts", ".test.ts")) for path in paths
        ):
            classifications.add(ChangeType.TEST_CHANGES)
        if any(
            path.endswith(("requirements.txt", "pyproject.toml", "package.json", "go.mod"))
            for path in paths
        ):
            classifications.add(ChangeType.DEPENDENCY_UPDATE)
        if any(
            path.startswith((".github/", "docker", "k8s/")) or "dockerfile" in path
            for path in paths
        ):
            classifications.add(ChangeType.INFRASTRUCTURE)
        if any(
            keyword in patches for keyword in ("auth", "encrypt", "permission", "token", "secret")
        ):
            classifications.add(ChangeType.SECURITY)
        if any(
            keyword in patches for keyword in ("fix", "bug", "regression", "exception", "error")
        ):
            classifications.add(ChangeType.BUG_FIX)
        if any(keyword in patches for keyword in ("rename", "extract", "cleanup", "refactor")):
            classifications.add(ChangeType.REFACTOR)
        if any(keyword in patches for keyword in ("add", "create", "new ", "feature")):
            classifications.add(ChangeType.FEATURE)

        return sorted(classifications or {ChangeType.FEATURE}, key=lambda item: item.value)

    def _score_risk(self, changed_files: list[dict[str, Any]]) -> RiskAssessment:
        total_files = len(changed_files)
        total_lines = sum(
            file_data.get("additions", 0) + file_data.get("deletions", 0)
            for file_data in changed_files
        )
        critical_files = [
            file_data["filename"]
            for file_data in changed_files
            if self._is_critical_path(file_data["filename"])
        ]
        patches = "\n".join(file_data.get("patch") or "" for file_data in changed_files).lower()

        score = min(25, total_files * 3) + min(30, total_lines // 20)
        drivers = [f"{total_files} files changed", f"{total_lines} lines changed"]

        if critical_files:
            score += min(20, len(critical_files) * 8)
            drivers.append("Critical files modified")
        if self._has_database_change(changed_files, patches):
            score += 15
            drivers.append("Database or migration changes detected")
        if self._has_security_sensitive_change(changed_files, patches):
            score += 15
            drivers.append("Security-sensitive code changed")
        if self._has_infrastructure_change(changed_files):
            score += 10
            drivers.append("Infrastructure changes detected")

        return RiskAssessment(score=min(100, score), drivers=drivers, critical_files=critical_files)

    def _dependency_graph(self, parsed_files: list[ParsedFile]) -> DependencyGraph:
        modified_functions: list[str] = []
        impacted_classes: list[str] = []
        edges: dict[str, list[str]] = {}

        for parsed_file in parsed_files:
            class_names = [symbol.name for symbol in parsed_file.symbols if symbol.kind == "class"]
            function_names = [
                symbol.name for symbol in parsed_file.symbols if symbol.kind == "function"
            ]
            modified_functions.extend(f"{parsed_file.path}:{name}" for name in function_names)
            impacted_classes.extend(f"{parsed_file.path}:{name}" for name in class_names)
            for class_name in class_names:
                edges[f"{parsed_file.path}:{class_name}"] = [
                    f"{parsed_file.path}:{function_name}" for function_name in function_names
                ]

        downstream_risks = []
        if impacted_classes:
            downstream_risks.append("Changed classes may affect constructor contracts and callers.")
        if len(modified_functions) >= 5:
            downstream_risks.append("Broad function churn increases regression surface area.")

        return DependencyGraph(
            modified_functions=modified_functions,
            impacted_classes=impacted_classes,
            edges=edges,
            downstream_risks=downstream_risks,
        )

    def _security_findings(self, changed_files: list[dict[str, Any]]) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        rules = [
            (
                "hardcoded_secret",
                "high",
                re.compile(r"(?i)(secret|password|api[_-]?key|token)\s*=\s*['\"][^'\"]{8,}"),
            ),
            (
                "dangerous_permission",
                "medium",
                re.compile(r"chmod\s+777|0o777|allow_all|public-read"),
            ),
            (
                "sql_injection",
                "high",
                re.compile(r"(?i)(execute|query)\([^)]*(f['\"]|%|\.format\()"),
            ),
            (
                "unsafe_shell",
                "high",
                re.compile(r"subprocess\.(run|popen|call).*shell\s*=\s*true|os\.system\("),
            ),
        ]
        for file_data in changed_files:
            path = file_data["filename"]
            for line_number, line in self._added_lines(file_data.get("patch") or ""):
                for rule, severity, pattern in rules:
                    if pattern.search(line):
                        findings.append(
                            SecurityFinding(
                                rule=rule,
                                severity=severity,
                                path=path,
                                line=line_number,
                                detail=self._redact_sensitive_value(line.strip()),
                            )
                        )
        return findings

    def _quality_scores(
        self,
        changed_files: list[dict[str, Any]],
        parsed_files: list[ParsedFile],
    ) -> QualityScores:
        total_lines = sum(
            file_data.get("additions", 0) + file_data.get("deletions", 0)
            for file_data in changed_files
        )
        security_findings = self._security_findings(changed_files)
        documented_symbols = sum(
            1 for parsed_file in parsed_files for symbol in parsed_file.symbols if symbol.docstring
        )
        total_symbols = sum(len(parsed_file.symbols) for parsed_file in parsed_files)

        documentation = (
            100 if total_symbols == 0 else round((documented_symbols / total_symbols) * 100)
        )
        complexity = max(0, 100 - min(70, total_lines // 4) - min(20, len(changed_files) * 2))
        maintainability = max(0, 100 - min(35, len(changed_files) * 4) - min(35, total_lines // 10))
        security = max(0, 100 - len(security_findings) * 25)
        overall = round(
            (documentation * 0.25)
            + (complexity * 0.25)
            + (maintainability * 0.25)
            + (security * 0.25)
        )

        return QualityScores(
            documentation_score=documentation,
            complexity_score=complexity,
            maintainability_score=maintainability,
            security_score=security,
            overall_quality_score=overall,
        )

    def _business_impact(
        self,
        repo_full_name: str,
        pr_number: int,
        result: PRIntelligenceResult,
    ) -> dict[str, Any]:
        classifications = ", ".join(change_type.value for change_type in result.classifications)
        deployment = "Standard deployment"
        if result.risk.score >= 70:
            deployment = "High-touch deployment with rollback plan and owner sign-off"
        elif result.risk.score >= 40:
            deployment = "Monitor after deploy and confirm automated coverage"

        return {
            "what_changed": f"{repo_full_name} #{pr_number} includes {classifications} changes.",
            "why_it_matters": "The PR may affect product behavior, operations, or code health.",
            "potential_impact": self._impact_label(result.risk.score),
            "deployment_considerations": deployment,
        }

    def _reports(
        self,
        repo_full_name: str,
        pr_number: int,
        result: PRIntelligenceResult,
    ) -> list[GeneratedDocument]:
        model = "pr_intelligence:deterministic"
        return [
            GeneratedDocument(
                title="pr_summary.md",
                content=self._summary_markdown(repo_full_name, pr_number, result),
                model=model,
                structured_output={
                    "classifications": [item.value for item in result.classifications]
                },
            ),
            GeneratedDocument(
                title="risk_report.md",
                content=self._risk_markdown(result),
                model=model,
                structured_output={"risk_score": result.risk.score},
            ),
            GeneratedDocument(
                title="security_report.md",
                content=self._security_markdown(result),
                model=model,
                structured_output={"finding_count": len(result.security_findings)},
            ),
            GeneratedDocument(
                title="impact_analysis.md",
                content=self._impact_markdown(result),
                model=model,
                structured_output=result.business_impact,
            ),
            GeneratedDocument(
                title="quality_report.md",
                content=self._quality_markdown(result),
                model=model,
                structured_output=result.quality.__dict__,
            ),
        ]

    @staticmethod
    def _summary_markdown(
        repo_full_name: str,
        pr_number: int,
        result: PRIntelligenceResult,
    ) -> str:
        classifications = ", ".join(item.value for item in result.classifications)
        return (
            f"# PR Intelligence Summary\n\n"
            f"Repository: `{repo_full_name}`\n\n"
            f"PR: `#{pr_number}`\n\n"
            f"## Change Classification\n{classifications}\n\n"
            f"## Risk\nScore: `{result.risk.score}/100`\n\n"
            f"## Business Impact\n{result.business_impact['what_changed']}\n"
            f"{result.business_impact['why_it_matters']}\n"
        )

    @staticmethod
    def _risk_markdown(result: PRIntelligenceResult) -> str:
        drivers = "\n".join(f"- {driver}" for driver in result.risk.drivers)
        critical = "\n".join(f"- `{path}`" for path in result.risk.critical_files) or "- None"
        downstream = (
            "\n".join(f"- {risk}" for risk in result.dependency_graph.downstream_risks) or "- None"
        )
        return (
            "# Risk Report\n\n"
            f"Risk score: `{result.risk.score}/100`\n\n"
            f"## Drivers\n{drivers}\n\n"
            f"## Critical Files\n{critical}\n\n"
            f"## Downstream Risks\n{downstream}\n"
        )

    @staticmethod
    def _security_markdown(result: PRIntelligenceResult) -> str:
        if not result.security_findings:
            findings = "- No security findings detected"
        else:
            findings = "\n".join(
                f"- `{finding.severity}` {finding.rule} in `{finding.path}`"
                f"{f':{finding.line}' if finding.line else ''}: {finding.detail}"
                for finding in result.security_findings
            )
        return f"# Security Report\n\n## Findings\n{findings}\n"

    @staticmethod
    def _impact_markdown(result: PRIntelligenceResult) -> str:
        impact = result.business_impact
        return (
            "# Business Impact Analysis\n\n"
            f"## What Changed\n{impact['what_changed']}\n\n"
            f"## Why It Matters\n{impact['why_it_matters']}\n\n"
            f"## Potential Impact\n{impact['potential_impact']}\n\n"
            f"## Deployment Considerations\n{impact['deployment_considerations']}\n"
        )

    @staticmethod
    def _quality_markdown(result: PRIntelligenceResult) -> str:
        quality = result.quality
        return (
            "# PR Quality Report\n\n"
            f"- Documentation score: `{quality.documentation_score}/100`\n"
            f"- Complexity score: `{quality.complexity_score}/100`\n"
            f"- Maintainability score: `{quality.maintainability_score}/100`\n"
            f"- Security score: `{quality.security_score}/100`\n"
            f"- Overall quality score: `{quality.overall_quality_score}/100`\n"
        )

    @staticmethod
    def _added_lines(patch: str) -> list[tuple[int | None, str]]:
        added: list[tuple[int | None, str]] = []
        new_line_number: int | None = None
        for line in patch.splitlines():
            if line.startswith("@@"):
                match = re.search(r"\+(\d+)", line)
                new_line_number = int(match.group(1)) if match else None
                continue
            if line.startswith("+") and not line.startswith("+++"):
                added.append((new_line_number, line[1:]))
                if new_line_number is not None:
                    new_line_number += 1
            elif line.startswith(" ") and new_line_number is not None:
                new_line_number += 1
        return added

    @staticmethod
    def _is_critical_path(path: str) -> bool:
        lowered = path.lower()
        return any(
            marker in lowered
            for marker in (
                "auth",
                "security",
                "payment",
                "billing",
                "migration",
                ".github/workflows",
                "dockerfile",
                "docker-compose",
                "k8s/",
            )
        )

    @staticmethod
    def _has_database_change(changed_files: list[dict[str, Any]], patches: str) -> bool:
        return any(
            "migration" in file_data["filename"].lower() for file_data in changed_files
        ) or any(keyword in patches for keyword in ("create table", "alter table", "drop table"))

    @staticmethod
    def _has_security_sensitive_change(changed_files: list[dict[str, Any]], patches: str) -> bool:
        return any("auth" in file_data["filename"].lower() for file_data in changed_files) or any(
            keyword in patches for keyword in ("password", "token", "secret", "permission")
        )

    @staticmethod
    def _has_infrastructure_change(changed_files: list[dict[str, Any]]) -> bool:
        return any(
            file_data["filename"].lower().startswith((".github/", "k8s/"))
            or "dockerfile" in file_data["filename"].lower()
            or file_data["filename"].lower().endswith(("docker-compose.yml", ".tf", ".yaml"))
            for file_data in changed_files
        )

    @staticmethod
    def _impact_label(score: int) -> str:
        if score >= 70:
            return "High operational or customer impact possible"
        if score >= 40:
            return "Moderate impact; targeted validation recommended"
        return "Low impact based on changed files and diff metadata"

    @staticmethod
    def _redact_sensitive_value(value: str) -> str:
        return re.sub(
            r"(?i)(secret|password|api[_-]?key|token)(\s*=\s*['\"])[^'\"]+(['\"])",
            r"\1\2[REDACTED]\3",
            value,
        )
