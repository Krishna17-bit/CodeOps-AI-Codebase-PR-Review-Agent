from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from radon.complexity import cc_visit
except Exception:  # pragma: no cover
    cc_visit = None

from .file_utils import iter_candidate_files, read_text
from .schemas import CodeFile, Finding, RepoAnalysis

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "React", ".ts": "TypeScript", ".tsx": "React TS",
    ".java": "Java", ".go": "Go", ".rs": "Rust", ".php": "PHP", ".rb": "Ruby", ".cs": "C#",
    ".cpp": "C++", ".c": "C", ".h": "C/C++ Header", ".hpp": "C++ Header", ".swift": "Swift",
    ".kt": "Kotlin", ".scala": "Scala", ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
    ".html": "HTML", ".css": "CSS", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML", ".ini": "Config", ".md": "Markdown", ".txt": "Text",
}

SECRET_PATTERNS = [
    ("Hardcoded API key-like value", re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]")),
    ("AWS access key pattern", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    ("Connection string with password", re.compile(r"(?i)(postgres|mysql|mongodb|redis)://[^\s]+:[^\s]+@")),
]

RISK_PATTERNS = [
    ("Dangerous eval/exec usage", "High", "Security", re.compile(r"\b(eval|exec)\s*\("), "Avoid executing dynamic strings. Use safe parsing or explicit dispatch."),
    ("subprocess with shell=True", "High", "Security", re.compile(r"subprocess\.[a-zA-Z_]+\([^\n]*shell\s*=\s*True"), "Avoid shell=True or pass trusted arguments as a list."),
    ("Unsafe pickle deserialization", "High", "Security", re.compile(r"\bpickle\.loads?\s*\("), "Do not unpickle untrusted data; use JSON or signed artifacts."),
    ("Unsafe YAML load", "High", "Security", re.compile(r"yaml\.load\s*\("), "Use yaml.safe_load for untrusted YAML."),
    ("Weak hash algorithm", "Medium", "Security", re.compile(r"hashlib\.(md5|sha1)\s*\("), "Use SHA-256 or stronger hashing unless legacy compatibility is required."),
    ("Broad exception hides failures", "Medium", "Reliability", re.compile(r"except\s+(Exception|BaseException)?\s*:\s*(pass|return|continue)?"), "Catch specific exceptions and log actionable details."),
    ("TODO/FIXME left in code", "Low", "Maintainability", re.compile(r"(?i)#?\s*(TODO|FIXME|HACK)"), "Convert TODOs into tracked issues or remove stale comments."),
    ("Debug print/log statement", "Low", "Maintainability", re.compile(r"\bprint\s*\("), "Use structured logging and avoid noisy debug output in production paths."),
    ("Potential SQL string concatenation", "High", "Security", re.compile(r"(?i)(select|insert|update|delete).*(\+|format\(|f['\"])"), "Use parameterized queries through your database driver/ORM."),
    ("CORS wildcard", "Medium", "Security", re.compile(r"(?i)(allow_origins\s*=\s*\[?['\"]\*|Access-Control-Allow-Origin['\"]\s*:\s*['\"]\*)"), "Avoid wildcard CORS for authenticated or sensitive endpoints."),
]

ENTRYPOINT_NAMES = {"app.py", "main.py", "server.py", "index.js", "server.js", "app.js", "manage.py", "streamlit_app.py"}
TEST_DIR_NAMES = {"tests", "test", "spec", "__tests__"}


def language_for(path: Path) -> str:
    if path.name == "Dockerfile":
        return "Dockerfile"
    if path.name == "requirements.txt":
        return "Python deps"
    if path.name == "package.json":
        return "Node deps"
    return LANG_BY_EXT.get(path.suffix.lower(), "Other")


def is_test_file(rel: str) -> bool:
    parts = set(Path(rel).parts)
    name = Path(rel).name.lower()
    return bool(parts & TEST_DIR_NAMES) or name.startswith("test_") or name.endswith("_test.py") or name.endswith(".test.js") or name.endswith(".spec.js") or name.endswith(".test.ts") or name.endswith(".spec.ts")


def parse_python(content: str) -> tuple[list[str], list[str], list[str], float]:
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []
    complexity = 0.0
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
        if cc_visit:
            blocks = cc_visit(content)
            if blocks:
                complexity = round(sum(getattr(b, "complexity", 0) for b in blocks) / len(blocks), 2)
    except Exception:
        pass
    return imports, functions, classes, complexity


def parse_js_like(content: str) -> tuple[list[str], list[str], list[str]]:
    imports = []
    imports += re.findall(r"import\s+(?:[^;]+?)\s+from\s+['\"]([^'\"]+)['\"]", content)
    imports += re.findall(r"require\(['\"]([^'\"]+)['\"]\)", content)
    functions = re.findall(r"function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", content)
    functions += re.findall(r"(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", content)
    classes = re.findall(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", content)
    return imports, functions, classes


def load_code_files(root: Path, limit_files: int = 500) -> list[CodeFile]:
    files: list[CodeFile] = []
    for path in list(iter_candidate_files(root))[:limit_files]:
        rel = str(path.relative_to(root)).replace("\\", "/")
        content = read_text(path)
        loc = len([line for line in content.splitlines() if line.strip()])
        lang = language_for(path)
        imports: list[str] = []
        functions: list[str] = []
        classes: list[str] = []
        complexity = 0.0
        if path.suffix.lower() == ".py":
            imports, functions, classes, complexity = parse_python(content)
        elif path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
            imports, functions, classes = parse_js_like(content)
        files.append(CodeFile(rel, lang, path.suffix.lower(), content, loc, imports, functions, classes, complexity, is_test_file(rel)))
    return files


def detect_architecture(files: list[CodeFile], root: Path) -> dict[str, Any]:
    all_text = "\n".join(f.content[:5000] for f in files)
    file_paths = {f.path for f in files}
    frameworks: list[str] = []
    deps: list[str] = []
    package_json = next((f for f in files if f.path.endswith("package.json")), None)
    requirements = next((f for f in files if f.path.endswith("requirements.txt")), None)
    pyproject = next((f for f in files if f.path.endswith("pyproject.toml")), None)

    framework_checks = {
        "FastAPI": ["fastapi", "FastAPI("], "Flask": ["flask", "Flask("], "Django": ["django", "manage.py"],
        "Streamlit": ["import streamlit", "streamlit"], "React": ["react", "jsx", "tsx"], "Next.js": ["next"],
        "Express": ["express"], "Pandas": ["pandas", "pd."], "scikit-learn": ["sklearn", "scikit-learn"],
        "PyTorch": ["torch", "pytorch"], "TensorFlow": ["tensorflow", "keras"], "SQLAlchemy": ["sqlalchemy"],
    }
    low = all_text.lower()
    for name, tokens in framework_checks.items():
        if any(t.lower() in low for t in tokens):
            frameworks.append(name)

    if package_json:
        try:
            pkg = json.loads(package_json.content)
            deps.extend(list((pkg.get("dependencies") or {}).keys())[:30])
            deps.extend(list((pkg.get("devDependencies") or {}).keys())[:20])
        except Exception:
            pass
    if requirements:
        deps.extend([line.strip().split("==")[0].split(">=")[0] for line in requirements.content.splitlines() if line.strip() and not line.startswith("#")][:50])
    if pyproject:
        deps.append("pyproject.toml present")

    entrypoints = [f.path for f in files if Path(f.path).name in ENTRYPOINT_NAMES]
    test_files = [f.path for f in files if f.is_test]
    config_files = [f.path for f in files if Path(f.path).name.lower() in {"dockerfile", "docker-compose.yml", "requirements.txt", "package.json", "pyproject.toml", ".env.example", "settings.py"}]
    docs = [f.path for f in files if Path(f.path).name.lower() in {"readme.md", "readme.txt", "docs.md"} or f.path.lower().startswith("docs/")]

    return {
        "frameworks_detected": sorted(set(frameworks)),
        "entrypoints": entrypoints,
        "dependency_files": [p for p in ["package.json" if package_json else "", "requirements.txt" if requirements else "", "pyproject.toml" if pyproject else ""] if p],
        "notable_dependencies": sorted(set([d for d in deps if d]))[:40],
        "test_files_count": len(test_files),
        "test_files": test_files[:25],
        "config_files": config_files[:30],
        "documentation_files": docs[:30],
        "has_readme": any(Path(f.path).name.lower().startswith("readme") for f in files),
        "has_gitignore": (root / ".gitignore").exists(),
        "has_env_example": any(Path(f.path).name.lower() == ".env.example" for f in files),
    }


def resolve_internal_import(source: CodeFile, imp: str, all_paths: set[str]) -> str | None:
    if imp.startswith(".") or imp.startswith("/"):
        return None
    parts = imp.split(".")
    candidates = ["/".join(parts) + ".py", "/".join(parts) + "/__init__.py"]
    if imp.startswith("./") or imp.startswith("../"):
        base = Path(source.path).parent
        candidate = str((base / imp).with_suffix(".js")).replace("\\", "/")
        candidates.extend([candidate, candidate.replace(".js", ".ts"), candidate.replace(".js", ".tsx"), candidate.replace(".js", ".jsx")])
    for cand in candidates:
        if cand in all_paths:
            return cand
    tail = parts[-1] if parts else imp
    matches = [p for p in all_paths if Path(p).stem == tail]
    if len(matches) == 1:
        return matches[0]
    return None


def build_dependency_edges(files: list[CodeFile]) -> list[tuple[str, str, str]]:
    paths = {f.path for f in files}
    edges = []
    for f in files:
        for imp in f.imports[:40]:
            target = resolve_internal_import(f, imp, paths)
            if target:
                edges.append((f.path, target, imp))
    return sorted(set(edges))[:300]


def line_number(content: str, pattern: re.Pattern[str]) -> tuple[int | None, str]:
    for i, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            return i, line.strip()[:240]
    m = pattern.search(content)
    if m:
        return None, m.group(0)[:240]
    return None, ""


def scan_findings(files: list[CodeFile], architecture: dict[str, Any]) -> tuple[list[Finding], list[Finding], list[Finding]]:
    risk: list[Finding] = []
    security: list[Finding] = []
    tests: list[Finding] = []

    for f in files:
        if f.loc > 450 and not f.is_test:
            risk.append(Finding("Large file / possible low cohesion", "Medium", "Maintainability", f.path, None, f"{f.loc} non-empty lines", "Split by responsibility and move reusable logic into smaller modules."))
        if f.complexity >= 9:
            risk.append(Finding("High average cyclomatic complexity", "Medium", "Maintainability", f.path, None, f"Average complexity: {f.complexity}", "Refactor deeply nested functions into smaller units and add tests for branches."))
        for title, sev, cat, pattern, rec in RISK_PATTERNS:
            ln, evidence = line_number(f.content, pattern)
            if evidence:
                finding = Finding(title, sev, cat, f.path, ln, evidence, rec)
                if cat == "Security":
                    security.append(finding)
                else:
                    risk.append(finding)
        for title, pattern in SECRET_PATTERNS:
            ln, evidence = line_number(f.content, pattern)
            if evidence:
                security.append(Finding(title, "Critical", "Secrets", f.path, ln, evidence, "Move secret values to environment variables, rotate exposed keys, and keep .env ignored."))

    if not architecture.get("has_readme"):
        risk.append(Finding("Missing README", "Medium", "Documentation", "repo", None, "No README file detected", "Add a README with setup, architecture, env vars, and usage."))
    if not architecture.get("has_gitignore"):
        risk.append(Finding("Missing .gitignore", "High", "Repo hygiene", "repo", None, "No .gitignore detected", "Add .gitignore to protect .env, venv, caches, outputs, logs, and secrets."))
    if not architecture.get("has_env_example"):
        risk.append(Finding("Missing .env.example", "Medium", "Developer experience", "repo", None, "No .env.example detected", "Add a safe example env file without real credentials."))

    non_test_functions = [(f, name) for f in files if not f.is_test for name in f.functions]
    test_text = "\n".join(f.content for f in files if f.is_test).lower()
    if not architecture.get("test_files_count"):
        tests.append(Finding("No test suite detected", "High", "Test gap", "repo", None, "No tests/ or test files found", "Add unit tests for core business logic, parsers, API routes, and error handling."))
    else:
        uncovered = []
        for f, fn in non_test_functions[:200]:
            if fn.lower() not in test_text and not fn.startswith("_"):
                uncovered.append((f.path, fn))
        for path, fn in uncovered[:25]:
            tests.append(Finding("Function appears uncovered by tests", "Medium", "Test gap", path, None, fn, "Add at least one unit test covering normal and failure paths."))

    return risk[:120], security[:120], tests[:120]


def generate_unit_tests(files: list[CodeFile]) -> dict[str, str]:
    tests: dict[str, str] = {}
    py_files = [f for f in files if f.extension == ".py" and not f.is_test and f.functions]
    for f in py_files[:8]:
        module = f.path[:-3].replace("/", ".")
        visible_functions = [fn for fn in f.functions if not fn.startswith("_")][:5]
        if not visible_functions:
            continue
        content = [
            "import pytest",
            "",
            f"# Generated starter tests for {f.path}",
            "# Review imports and fixtures before using in production.",
            f"import {module} as module_under_test",
            "",
        ]
        for fn in visible_functions:
            content.extend([
                f"def test_{fn}_basic_behavior():",
                f"    assert hasattr(module_under_test, '{fn}')",
                "    # TODO: replace this smoke test with real inputs and expected output",
                "",
                f"def test_{fn}_handles_invalid_input():",
                "    # TODO: assert the function handles invalid or empty inputs safely",
                "    assert True",
                "",
            ])
        tests[f"tests/test_{Path(f.path).stem}.py"] = "\n".join(content)
    if not tests:
        tests["tests/test_smoke.py"] = """def test_repository_smoke():\n    # Generated placeholder. Add focused tests for core modules.\n    assert True\n"""
    return tests


def make_refactor_plan(files: list[CodeFile], risk: list[Finding], security: list[Finding], tests: list[Finding]) -> list[dict[str, Any]]:
    hotspots = sorted([f for f in files if not f.is_test], key=lambda x: (x.loc, x.complexity), reverse=True)[:6]
    plan: list[dict[str, Any]] = []
    if security:
        plan.append({"priority": "P0", "area": "Security hardening", "why": f"{len(security)} security finding(s) detected", "actions": ["Remove hardcoded secrets", "Replace unsafe deserialization/execution patterns", "Parameterize database queries", "Add security regression tests"]})
    if tests:
        plan.append({"priority": "P1", "area": "Test coverage", "why": f"{len(tests)} test gap finding(s) detected", "actions": ["Add pytest/Jest test suite", "Cover parsers and business logic", "Add failure-path and edge-case tests", "Run tests in CI"]})
    if hotspots:
        plan.append({"priority": "P1", "area": "Complexity and modularity", "why": "Large or complex files can slow delivery and increase bug risk", "actions": [f"Refactor {h.path} ({h.loc} LOC, complexity {h.complexity})" for h in hotspots[:4]]})
    plan.append({"priority": "P2", "area": "Documentation and onboarding", "why": "Clients and new developers need fast setup and architecture context", "actions": ["Keep README current", "Document environment variables", "Add architecture diagram and data-flow notes", "Document release/test commands"]})
    return plan


def make_readme(repo_name: str, files: list[CodeFile], architecture: dict[str, Any], risk: list[Finding], security: list[Finding], tests: list[Finding]) -> str:
    frameworks = ", ".join(architecture.get("frameworks_detected") or ["Not clearly detected"])
    entrypoints = "\n".join(f"- `{p}`" for p in architecture.get("entrypoints", [])) or "- Not clearly detected"
    deps = "\n".join(f"- {d}" for d in architecture.get("notable_dependencies", [])[:15]) or "- Not clearly detected"
    return f"""# {repo_name}

## Overview
This repository appears to be a software project using: **{frameworks}**.

## Entry points
{entrypoints}

## Notable dependencies
{deps}

## Repository health snapshot
- Files analyzed: {len(files)}
- Security findings: {len(security)}
- Maintainability / risk findings: {len(risk)}
- Test gap findings: {len(tests)}

## Suggested setup
```bash
# Install dependencies using the package manager used by this repo
# Example for Python:
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Suggested quality checks
```bash
pytest
python -m compileall .
```

## Notes
This README was generated by CodeOps AI and should be reviewed by a human before publishing.
"""


def analyze_repo(root: Path, repo_name: str, limit_files: int = 500) -> RepoAnalysis:
    files = load_code_files(root, limit_files=limit_files)
    language_counts = Counter(f.language for f in files)
    loc_by_language: dict[str, int] = defaultdict(int)
    for f in files:
        loc_by_language[f.language] += f.loc
    architecture = detect_architecture(files, root)
    edges = build_dependency_edges(files)
    risk, security, tests = scan_findings(files, architecture)
    generated = generate_unit_tests(files)
    refactor_plan = make_refactor_plan(files, risk, security, tests)
    readme = make_readme(repo_name, files, architecture, risk, security, tests)
    audit = {
        "repo_name": repo_name,
        "files_analyzed": len(files),
        "languages": dict(language_counts),
        "security_findings": len(security),
        "risk_findings": len(risk),
        "test_gap_findings": len(tests),
        "human_review_required": bool(security or tests),
    }
    return RepoAnalysis(repo_name, files, dict(language_counts), dict(loc_by_language), architecture, edges, risk, security, tests, generated, refactor_plan, readme, audit)
