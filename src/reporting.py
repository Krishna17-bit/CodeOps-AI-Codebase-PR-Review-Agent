from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from .schemas import Finding, RepoAnalysis


def findings_to_df(findings: list[Finding]) -> pd.DataFrame:
    if not findings:
        return pd.DataFrame(columns=["severity", "category", "title", "file_path", "line", "evidence", "recommendation"])
    return pd.DataFrame([
        {
            "severity": f.severity,
            "category": f.category,
            "title": f.title,
            "file_path": f.file_path,
            "line": f.line or "",
            "evidence": f.evidence,
            "recommendation": f.recommendation,
        }
        for f in findings
    ])


def make_markdown_report(analysis: RepoAnalysis, narrative: str = "") -> str:
    arch = analysis.architecture
    lines = [
        f"# CodeOps AI Review Report — {analysis.repo_name}",
        "",
        "## Executive summary",
        narrative or "Static codebase analysis completed.",
        "",
        "## Repository snapshot",
        f"- Files analyzed: {len(analysis.files)}",
        f"- Languages: {', '.join([f'{k} ({v})' for k, v in analysis.language_counts.items()]) or 'Not detected'}",
        f"- Frameworks detected: {', '.join(arch.get('frameworks_detected') or ['Not clearly detected'])}",
        f"- Entry points: {', '.join(arch.get('entrypoints') or ['Not clearly detected'])}",
        f"- Test files: {arch.get('test_files_count', 0)}",
        f"- Security findings: {len(analysis.security_findings)}",
        f"- Risk findings: {len(analysis.risk_findings)}",
        f"- Test gap findings: {len(analysis.test_gap_findings)}",
        "",
        "## Priority refactor plan",
    ]
    for item in analysis.refactor_plan:
        lines.append(f"### {item['priority']} — {item['area']}")
        lines.append(f"Why: {item['why']}")
        for action in item.get("actions", []):
            lines.append(f"- {action}")
        lines.append("")
    for title, findings in [("Security findings", analysis.security_findings), ("Maintainability/risk findings", analysis.risk_findings), ("Test gap findings", analysis.test_gap_findings)]:
        lines.append(f"## {title}")
        if not findings:
            lines.append("No findings detected by local scan.")
        for f in findings[:40]:
            loc = f" line {f.line}" if f.line else ""
            lines.append(f"- **{f.severity}** `{f.file_path}`{loc}: {f.title}. Evidence: `{f.evidence}` Recommendation: {f.recommendation}")
        lines.append("")
    lines.append("## Generated README draft")
    lines.append(analysis.readme_draft)
    return "\n".join(lines)


def save_exports(base_dir: Path, analysis: RepoAnalysis, narrative: str = "") -> dict[str, Path]:
    out_dir = base_dir / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "codeops_audit.json"
    report_path = out_dir / "codeops_review_report.md"
    risk_csv = out_dir / "codeops_findings.csv"
    tests_dir = out_dir / "generated_tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    json_path.write_text(json.dumps(analysis.to_dict(), indent=2, default=str), encoding="utf-8")
    report_path.write_text(make_markdown_report(analysis, narrative), encoding="utf-8")
    combined = analysis.security_findings + analysis.risk_findings + analysis.test_gap_findings
    findings_to_df(combined).to_csv(risk_csv, index=False)

    for rel, content in analysis.generated_tests.items():
        path = tests_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    zip_tests = out_dir / "generated_tests.zip"
    if zip_tests.exists():
        zip_tests.unlink()
    with zipfile.ZipFile(zip_tests, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in tests_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(tests_dir))

    return {"json": json_path, "report": report_path, "findings_csv": risk_csv, "tests_zip": zip_tests}
