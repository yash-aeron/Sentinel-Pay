"""Language-model backends behind one Protocol.

``NullLLM`` is deterministic and offline: it answers extractively from the
retrieved policy text in the prompt, so tests and CI never need a model and
grounding holds by construction. ``OllamaLLM`` talks to a local Ollama
(native on this Windows laptop; GPU passthrough into WSL2 is unreliable, so
Ollama is deliberately not containerised).
"""

from __future__ import annotations

import re
from typing import Protocol

import httpx

from app.core.errors import CopilotUnavailable

CHUNK_START_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class LLM(Protocol):
    name: str

    async def complete(self, prompt: str) -> str: ...


def _prompt_chunks(prompt: str) -> list[tuple[str, str]]:
    """Parse the CONTEXT blocks into ``(chunk_id, body_text)`` pairs.

    ``_render_context`` writes ``[id] title - heading (metadata)`` as the
    first line and the chunk text below it. The header carries dates and ids
    that are *not* part of the validated retrieved text, so only the body is
    returned -- quoting the header would produce answers the validator
    correctly rejects.
    """
    chunks: list[tuple[str, str]] = []
    current_id: str | None = None
    lines: list[str] = []
    for line in prompt.splitlines():
        if line.startswith(("TRANSACTION:", "QUESTION:")):
            break  # context ended; never let the payment JSON become "evidence"
        match = CHUNK_START_RE.match(line)
        if match:
            if current_id is not None:
                chunks.append((current_id, "\n".join(lines).strip()))
            current_id, lines = match.group(1), []
        elif current_id is not None and not line.strip():
            if lines and lines[-1].strip():
                continue  # paragraph break inside the chunk: keep scanning
            chunks.append((current_id, "\n".join(lines).strip()))
            current_id, lines = None, []
        elif current_id is not None:
            lines.append(line)
    if current_id is not None:
        chunks.append((current_id, "\n".join(lines).strip()))
    return chunks


class NullLLM:
    """Extractive stand-in: quote the retrieved chunk that best matches."""

    name = "null"

    async def complete(self, prompt: str) -> str:
        question = ""
        if "QUESTION: " in prompt:
            question = prompt.rsplit("QUESTION: ", 1)[1].splitlines()[0].lower()
        keywords = {word for word in re.findall(r"[a-z0-9]+", question) if len(word) > 3}

        best_id, best_text, best_score = "", "", -1
        for chunk_id, text in _prompt_chunks(prompt):
            lowered = text.lower()
            score = sum(lowered.count(word) for word in keywords)
            if score > best_score:
                best_id, best_text, best_score = chunk_id, text, score

        if not best_id:
            return "Insufficient evidence in the retrieved policy set."

        sentences = SENTENCE_SPLIT_RE.split(best_text)
        quoted = " ".join(sentences[:2]).strip()
        return f"[{best_id}] {quoted}"


class OllamaLLM:
    name: str

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: int = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._client = client
        self.name = f"ollama:{model}"

    async def complete(self, prompt: str) -> str:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
            payload = response.json()
            answer = str(payload.get("response", "")).strip()
            if not answer:
                raise CopilotUnavailable("the model returned an empty answer")
            return answer
        except httpx.HTTPError as exc:
            raise CopilotUnavailable(f"ollama is unreachable at {self._base_url}") from exc
        finally:
            if self._client is None:
                await client.aclose()


def build_llm(provider: str, settings_llm: dict[str, str | int]) -> LLM:
    if provider == "null":
        return NullLLM()
    if provider == "ollama":
        return OllamaLLM(
            str(settings_llm["base_url"]),
            str(settings_llm["model"]),
            timeout_seconds=int(settings_llm["timeout_seconds"]),
        )
    raise ValueError(f"unknown llm provider: {provider}")


__all__ = ["LLM", "NullLLM", "OllamaLLM", "build_llm"]
