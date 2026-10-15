"""Policy corpus: markdown files with frontmatter, chunked on headings.

Chunk ids are stable across re-indexes (``POL-001#s2``), which is what makes
the Chroma upsert idempotent and lets a citation in an old answer still
resolve to the chunk it named.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    doc_id: str
    title: str
    jurisdiction: str
    effective_date: str
    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    title: str
    jurisdiction: str
    effective_date: str
    body: str


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(raw)
    if match is None:
        raise ValueError("policy file is missing frontmatter")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key and value:
            meta[key.strip()] = value.strip()
    return meta, raw[match.end():]


def parse_document(path: Path) -> Document:
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    for required in ("id", "title", "jurisdiction", "effective_date"):
        if required not in meta:
            raise ValueError(f"{path.name}: frontmatter is missing '{required}'")
    return Document(
        id=meta["id"],
        title=meta["title"],
        jurisdiction=meta["jurisdiction"],
        effective_date=meta["effective_date"],
        body=body,
    )


def chunk_document(document: Document) -> list[Chunk]:
    """One chunk per ``##`` heading; text before the first heading is the intro."""
    matches = list(HEADING_RE.finditer(document.body))
    boundaries: list[tuple[str, str, int, int]] = []
    if matches and matches[0].start() > 0:
        intro = document.body[: matches[0].start()].strip()
        if intro:
            boundaries.append((f"{document.id}#s0", "Introduction", 0, matches[0].start()))
    for index, match in enumerate(matches, start=1):
        start = match.end()
        end = matches[index].start() if index < len(matches) else len(document.body)
        boundaries.append((f"{document.id}#s{index}", match.group(1).strip(), start, end))

    chunks: list[Chunk] = []
    for chunk_id, heading, start, end in boundaries:
        text = document.body[start:end].strip()
        if not text:
            continue
        chunks.append(
            Chunk(
                id=chunk_id,
                doc_id=document.id,
                title=document.title,
                jurisdiction=document.jurisdiction,
                effective_date=document.effective_date,
                heading=heading,
                text=text,
            )
        )
    return chunks


def load_corpus(policies_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(policies_dir.glob("*.md")):
        chunks.extend(chunk_document(parse_document(path)))
    if not chunks:
        raise ValueError(f"no policy documents found in {policies_dir}")
    return chunks


__all__ = ["Chunk", "Document", "chunk_document", "load_corpus", "parse_document"]
