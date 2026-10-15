"""Vector store over the policy corpus, backed by an embedded Chroma client.

The embedding function is injected so tests and CI can run fully offline
with a deterministic hash embedder, while the demo laptop uses Chroma's
ONNX MiniLM (downloaded once on first use, no API key, no torch).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from app.services.copilot.corpus import Chunk

COLLECTION = "compliance_policies"
DIMENSIONS = 256


@dataclass(frozen=True, slots=True)
class Retrieved:
    chunk_id: str
    doc_id: str
    title: str
    heading: str
    jurisdiction: str
    effective_date: str
    text: str
    distance: float


class Embedder(EmbeddingFunction[Documents]):
    """Chroma's embedding contract plus a name() probe for logging.

    The chroma base class declares ``name`` as an attribute but this version
    calls it as a method at runtime; subclasses define it as a method and
    carry targeted type: ignore comments where the stubs disagree.
    """

    def name(self) -> str:  # type: ignore[override]
        raise NotImplementedError


class HashingEmbeddings(Embedder):
    """Deterministic offline embedder: a bag of hashed word tokens.

    Not semantic. It ranks lexical overlap, which is exactly what the
    grounding validator and the corpus (where the operative words are the
    thresholds themselves) need from an offline test embedder.
    """

    def name(self) -> str:  # type: ignore[override]
        return "hashing-v1"

    def __init__(self) -> None:
        # chromadb's EmbeddingFunction base will require __init__ overrides.
        super().__init__()

    def __call__(self, input: Documents) -> Embeddings:  # noqa: A002 - chroma API
        vectors: list[list[float]] = []
        for text in input:
            vector = [0.0] * DIMENSIONS
            for token in text.lower().split():
                digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:2], "big") % DIMENSIONS
                vector[index] += 1.0
            norm = sum(vector) ** 0.5 or 1.0
            vectors.append([value / norm for value in vector])
        return vectors  # type: ignore[return-value]


class MiniLMEmbeddings(Embedder):
    """Chroma's default ONNX MiniLM embedder under our own name.

    Downloads the model once on first use: no API key, no torch. Offline
    environments and tests use ``HashingEmbeddings`` instead.
    """

    def name(self) -> str:  # type: ignore[override]
        return "minilm-onnx"

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        self._default = DefaultEmbeddingFunction()

    def __call__(self, input: Documents) -> Embeddings:  # noqa: A002 - chroma API
        return self._default(input=input)


class ChromaPolicyStore:
    def __init__(self, path: Path, embeddings: Embedder) -> None:
        client = chromadb.PersistentClient(path=str(path))
        self._collection = client.get_or_create_collection(
            COLLECTION, embedding_function=embeddings  # type: ignore[arg-type]
        )
        self._embeddings = embeddings

    @property
    def embedding_name(self) -> str:
        return self._embeddings.name()

    def index(self, chunks: list[Chunk]) -> int:
        """Upsert every chunk. Idempotent on stable chunk ids."""
        if not chunks:
            return 0
        self._collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=[
                {
                    "doc_id": chunk.doc_id,
                    "title": chunk.title,
                    "heading": chunk.heading,
                    "jurisdiction": chunk.jurisdiction,
                    "effective_date": chunk.effective_date,
                }
                for chunk in chunks
            ],
        )
        return len(chunks)

    def search(self, query: str, k: int = 4) -> list[Retrieved]:
        result: Any = self._collection.query(query_texts=[query], n_results=k)
        hits: list[Retrieved] = []
        ids = result["ids"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]
        for chunk_id, text, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=True
        ):
            hits.append(
                Retrieved(
                    chunk_id=str(chunk_id),
                    doc_id=str(metadata["doc_id"]),
                    title=str(metadata["title"]),
                    heading=str(metadata["heading"]),
                    jurisdiction=str(metadata.get("jurisdiction", "global")),
                    effective_date=str(metadata.get("effective_date", "")),
                    text=str(text),
                    distance=float(distance),
                )
            )
        return hits

    def count(self) -> int:
        return int(self._collection.count())


def build_embeddings(kind: str) -> Embedder:
    """Factory for the configured embedder.

    ``minilm`` uses Chroma's default ONNX embedder (no API key, no torch;
    downloads the model once on first use). ``hashing`` is the offline
    deterministic embedder used by tests and CI.
    """
    if kind == "hashing":
        return HashingEmbeddings()
    if kind == "minilm":
        return MiniLMEmbeddings()
    raise ValueError(f"unknown embedding provider: {kind}")


__all__ = [
    "COLLECTION",
    "ChromaPolicyStore",
    "HashingEmbeddings",
    "MiniLMEmbeddings",
    "Retrieved",
    "build_embeddings",
]
