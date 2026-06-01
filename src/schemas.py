from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CodeFile:
    path: str
    language: str
    extension: str
    content: str
    loc: int
    imports: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    complexity: float = 0.0
    is_test: bool = False


@dataclass
class Finding:
    title: str
    severity: str
    category: str
    file_path: str
    line: int | None
    evidence: str
    recommendation: str


@dataclass
class RepoAnalysis:
    repo_name: str
    files: list[CodeFile]
    language_counts: dict[str, int]
    loc_by_language: dict[str, int]
    architecture: dict[str, Any]
    dependency_edges: list[tuple[str, str, str]]
    risk_findings: list[Finding]
    security_findings: list[Finding]
    test_gap_findings: list[Finding]
    generated_tests: dict[str, str]
    refactor_plan: list[dict[str, Any]]
    readme_draft: str
    audit: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data
