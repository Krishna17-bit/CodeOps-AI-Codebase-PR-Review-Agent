from __future__ import annotations

from .schemas import CodeFile


def make_chunks(files: list[CodeFile], max_chars: int = 2800, overlap: int = 300) -> list[dict]:
    chunks: list[dict] = []
    for f in files:
        content = f.content
        if not content.strip():
            continue
        if len(content) <= max_chars:
            chunks.append({"path": f.path, "language": f.language, "content": content})
            continue
        start = 0
        idx = 1
        while start < len(content):
            end = start + max_chars
            chunks.append({"path": f"{f.path}#chunk-{idx}", "language": f.language, "content": content[start:end]})
            start = max(0, end - overlap)
            idx += 1
            if idx > 20:
                break
    return chunks
