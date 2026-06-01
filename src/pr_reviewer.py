from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from .llm_client import AIReasoningClient


@dataclass
class DiffFile:
    path: str
    additions: int
    deletions: int
    hunks: int
    risky_lines: list[str]


@dataclass
class PRReview:
    files: list[DiffFile]
    summary: dict[str, Any]
    comments: list[dict[str, Any]]
    ai_review: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


RISKY_DIFF_PATTERNS = [
    ("Possible secret added", "Critical", re.compile(r"(?i)^\+.*(api[_-]?key|secret|token|password)\s*[:=]")),
    ("Auth/security-sensitive change", "High", re.compile(r"(?i)(auth|jwt|oauth|password|permission|role|admin|security)")),
    ("Payment/billing-sensitive change", "High", re.compile(r"(?i)(payment|billing|invoice|stripe|refund|price)")),
    ("Unsafe dynamic execution added", "High", re.compile(r"^\+.*\b(eval|exec)\s*\(")),
    ("subprocess shell=True added", "High", re.compile(r"^\+.*shell\s*=\s*True")),
    ("Test removed or weakened", "Medium", re.compile(r"^-.*(assert|expect|pytest|unittest|describe\(|it\()")),
    ("TODO/FIXME added", "Low", re.compile(r"(?i)^\+.*(TODO|FIXME|HACK)")),
]


def parse_unified_diff(diff_text: str) -> list[DiffFile]:
    files: list[DiffFile] = []
    current_path = "unknown"
    additions = deletions = hunks = 0
    risky: list[str] = []

    def flush() -> None:
        nonlocal additions, deletions, hunks, risky, current_path
        if current_path != "unknown" or additions or deletions or hunks:
            files.append(DiffFile(current_path, additions, deletions, hunks, risky[:20]))
        additions = deletions = hunks = 0
        risky = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush()
            parts = line.split()
            current_path = parts[-1][2:] if len(parts) >= 4 and parts[-1].startswith("b/") else parts[-1] if parts else "unknown"
            continue
        if line.startswith("+++ b/"):
            current_path = line[6:]
        if line.startswith("@@"):
            hunks += 1
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        if line.startswith("-") and not line.startswith("---"):
            deletions += 1
        for title, sev, pattern in RISKY_DIFF_PATTERNS:
            try:
                matched = pattern.search(line)
            except re.error:
                matched = None
            if matched:
                risky.append(f"{sev}: {title}: {line[:180]}")
    flush()
    return [f for f in files if f.additions or f.deletions or f.hunks or f.risky_lines]


def review_diff(diff_text: str, client: AIReasoningClient | None = None) -> PRReview:
    files = parse_unified_diff(diff_text)
    comments: list[dict[str, Any]] = []
    total_add = sum(f.additions for f in files)
    total_del = sum(f.deletions for f in files)
    for f in files:
        if f.additions + f.deletions > 300:
            comments.append({"severity": "Medium", "file": f.path, "comment": "Large change set. Ask for smaller PR or stronger test evidence."})
        if f.risky_lines:
            for item in f.risky_lines[:6]:
                sev = item.split(":", 1)[0]
                comments.append({"severity": sev, "file": f.path, "comment": item})
        if "test" not in f.path.lower() and (f.additions + f.deletions) > 30:
            comments.append({"severity": "Medium", "file": f.path, "comment": "Non-trivial code change. Confirm matching unit/integration tests were added."})
    summary = {
        "files_changed": len(files),
        "additions": total_add,
        "deletions": total_del,
        "risk_comments": len(comments),
        "human_review_required": bool(comments),
    }
    ai_review = ""
    if client and client.configured and diff_text.strip():
        prompt = f"""Review this pull-request diff as a senior engineer. Give concise sections: Summary, Risks, Test requests, Approval recommendation. Do not mention any model/provider.

DIFF:
{diff_text[:16000]}
"""
        resp = client.complete(prompt, max_output_tokens=1600)
        if resp.ok:
            ai_review = resp.text.strip()
    if not ai_review:
        ai_review = "Local PR review completed. Check flagged comments, sensitive areas, and whether tests match the code changes."
    return PRReview(files, summary, comments[:80], ai_review)
