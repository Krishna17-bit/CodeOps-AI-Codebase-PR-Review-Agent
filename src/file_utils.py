from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".next", ".turbo", "coverage",
}
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyo", ".class", ".jar", ".db", ".sqlite",
}
SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".php", ".rb", ".cs",
    ".cpp", ".c", ".h", ".hpp", ".swift", ".kt", ".scala", ".sql", ".sh", ".ps1",
    ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".ini", ".md", ".txt", ".env.example",
}


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_path = dest_dir / member.filename
            resolved = member_path.resolve()
            if not str(resolved).startswith(str(dest_dir.resolve())):
                raise ValueError(f"Unsafe zip path blocked: {member.filename}")
        zf.extractall(dest_dir)


def find_repo_root(extract_dir: Path) -> Path:
    children = [p for p in extract_dir.iterdir() if p.is_dir() and p.name not in IGNORE_DIRS]
    files = [p for p in extract_dir.iterdir() if p.is_file()]
    if len(children) == 1 and not files:
        return children[0]
    return extract_dir


def iter_candidate_files(root: Path, max_file_size_kb: int = 350) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for filename in filenames:
            path = Path(dirpath) / filename
            ext = path.suffix.lower()
            if ext in BINARY_EXTENSIONS:
                continue
            if ext not in SOURCE_EXTENSIONS and filename not in {"Dockerfile", "Makefile", "Procfile", "requirements.txt", "package.json", "pyproject.toml"}:
                continue
            try:
                if path.stat().st_size > max_file_size_kb * 1024:
                    continue
            except OSError:
                continue
            yield path


def read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=enc, errors="replace")
        except Exception:
            continue
    return ""


def write_uploaded_file(uploaded_file, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(uploaded_file.getbuffer())
    return path


def zip_directory(src_dir: Path, out_zip: Path) -> Path:
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in src_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(src_dir))
    return out_zip
