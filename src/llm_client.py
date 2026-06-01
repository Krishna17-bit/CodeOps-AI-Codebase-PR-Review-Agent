from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from dotenv import load_dotenv

load_dotenv()


@dataclass
class AIResponse:
    ok: bool
    text: str
    source: str = "local"


class AIReasoningClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-pro").strip() or "gemini-2.5-pro"
        self.configured = bool(self.api_key and self.api_key != "your_api_key_here")
        self._model = None
        if self.configured:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self._model = genai.GenerativeModel(self.model_name)
            except Exception:
                self._model = None
                self.configured = False

    @property
    def status_help(self) -> str:
        if self.configured:
            return "AI reasoning engine configured. Local static analysis is still used as the safety baseline."
        return "AI reasoning engine not configured. The app will run deterministic local code analysis only."

    def complete(self, prompt: str, max_output_tokens: int = 1600) -> AIResponse:
        if not self.configured or self._model is None:
            return AIResponse(False, "")
        try:
            response = self._model.generate_content(
                prompt,
                generation_config={"temperature": 0.2, "max_output_tokens": max_output_tokens},
            )
            return AIResponse(True, getattr(response, "text", "") or "")
        except Exception as exc:
            return AIResponse(False, f"AI request failed safely: {exc}")


def retrieve_code_context(question: str, chunks: Iterable[dict], limit: int = 8) -> list[dict]:
    terms = [t.lower() for t in question.replace("_", " ").replace("/", " ").split() if len(t) > 2]
    scored = []
    for chunk in chunks:
        text = (chunk.get("content") or "").lower()
        path = (chunk.get("path") or "").lower()
        score = 0
        for term in terms:
            score += text.count(term)
            if term in path:
                score += 4
        if score:
            scored.append((score, chunk))
    return [c for _, c in sorted(scored, key=lambda x: x[0], reverse=True)[:limit]]


def answer_code_question(client: AIReasoningClient, question: str, chunks: list[dict]) -> str:
    retrieved = retrieve_code_context(question, chunks)
    if not retrieved:
        return "I could not find strong matching code context. Try naming a file, module, function, or feature."
    context = "\n\n".join(
        f"FILE: {c['path']}\n```\n{c['content'][:2400]}\n```" for c in retrieved
    )
    if client.configured:
        prompt = f"""You are CodeOps AI, a careful codebase analysis assistant.
Answer only from the provided code context. If something is uncertain, say what is uncertain.
Include file-level references in plain text.

Question:
{question}

Code context:
{context}
"""
        resp = client.complete(prompt, max_output_tokens=1800)
        if resp.ok and resp.text.strip():
            return resp.text.strip()
    bullets = [f"- `{c['path']}` appears relevant because it matched your query terms." for c in retrieved[:6]]
    return "AI reasoning is not configured, so here is a local retrieval result:\n\n" + "\n".join(bullets)


def enrich_summary(client: AIReasoningClient, repo_brief: str) -> str:
    if not client.configured:
        return "AI narrative summary is unavailable because the reasoning engine is not configured. Static analysis results are still available below."
    prompt = f"""Write a concise engineering review summary for this repository analysis. Focus on architecture, risks, tests, and next actions. Avoid mentioning any model/provider.

{repo_brief}
"""
    resp = client.complete(prompt, max_output_tokens=1200)
    return resp.text.strip() if resp.ok and resp.text.strip() else "AI narrative summary could not be generated. Static analysis results are still available."
