"""Corpus parsing, offline embeddings, and the grounding validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.copilot.corpus import chunk_document, load_corpus, parse_document
from app.services.copilot.grounding import INSUFFICIENT, validate
from app.services.copilot.llm import NullLLM
from app.services.copilot.store import HashingEmbeddings, build_embeddings

POLICY = """---
id: POL-042
title: Test Policy
jurisdiction: US
effective_date: 2026-01-01
---

# Test Policy

## Purpose

Testing chunking. Six attempts in 60 seconds averages 300 minor units.

## Thresholds

The limit is 8 times the prior average. Numbers like 100000 and 0.45 appear
here so validators have things to check.
"""


def test_parse_document_extracts_frontmatter(tmp_path: Path):
    path = tmp_path / "POL-042-test.md"
    path.write_text(POLICY, encoding="utf-8")
    document = parse_document(path)
    assert document.id == "POL-042"
    assert document.title == "Test Policy"
    assert document.jurisdiction == "US"
    assert document.effective_date == "2026-01-01"


def test_missing_frontmatter_is_an_error(tmp_path: Path):
    path = tmp_path / "bad.md"
    path.write_text("# no frontmatter\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        parse_document(path)


def test_chunking_is_one_chunk_per_heading():
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "POL-042-test.md"
        path.write_text(POLICY, encoding="utf-8")
        document = parse_document(path)
    chunks = chunk_document(document)
    assert [chunk.id for chunk in chunks] == ["POL-042#s0", "POL-042#s1", "POL-042#s2"]
    assert [chunk.heading for chunk in chunks] == ["Introduction", "Purpose", "Thresholds"]
    assert "8 times" in chunks[2].text


def test_chunk_ids_are_stable_across_parses(tmp_path: Path):
    path = tmp_path / "POL-042-test.md"
    path.write_text(POLICY, encoding="utf-8")
    first = chunk_document(parse_document(path))
    second = chunk_document(parse_document(path))
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]


def test_repo_corpus_loads_nine_documents():
    repo_policies = Path(__file__).resolve().parents[3] / "policies"
    if not repo_policies.is_dir():
        pytest.skip("policies corpus not present")
    chunks = load_corpus(repo_policies)
    doc_ids = {chunk.doc_id for chunk in chunks}
    assert len(doc_ids) == 9
    assert all(chunk.id.startswith(doc_id) for chunk in chunks for doc_id in [chunk.doc_id])


def test_hashing_embeddings_are_deterministic_and_normalized():
    import numpy as np

    embedder = HashingEmbeddings()
    vectors = list(embedder(["velocity thresholds", "velocity thresholds", "different"]))
    assert np.array_equal(vectors[0], vectors[1])
    assert not np.array_equal(vectors[0], vectors[2])
    for vector in vectors:
        assert abs(float(np.sum(vector * vector)) - 1.0) < 1e-5


def test_build_embeddings_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown embedding provider"):
        build_embeddings("moonlight")


def test_validate_accepts_a_grounded_answer():
    grounded, violations = validate(
        "Per [POL-001#s2], blocking applies inside 60 seconds.",
        retrieved_ids={"POL-001#s2", "POL-001#s3"},
        retrieved_text="Six or more attempts inside 60 seconds must be blocked.",
        allowed_numbers=set(),
    )
    assert grounded and violations == []


def test_validate_rejects_unretrieved_citations():
    grounded, violations = validate(
        "Per [POL-099#s1], block everything.",
        retrieved_ids={"POL-001#s2"},
        retrieved_text="irrelevant",
        allowed_numbers=set(),
    )
    assert not grounded
    assert any("POL-099#s1" in violation for violation in violations)


def test_validate_rejects_invented_numbers():
    grounded, violations = validate(
        "Block at [POL-001#s2] after 42 attempts.",
        retrieved_ids={"POL-001#s2"},
        retrieved_text="Six or more attempts inside 60 seconds must be blocked.",
        allowed_numbers=set(),
    )
    assert not grounded
    assert any("42" in violation for violation in violations)


def test_validate_allows_transaction_numbers():
    grounded, _ = validate(
        "The payment of 2500 was held per [POL-003#s1].",
        retrieved_ids={"POL-003#s1"},
        retrieved_text="A payment scored into the review band is held.",
        allowed_numbers={"2500"},
    )
    assert grounded


async def test_null_llm_answers_extractively_with_citation():
    prompt = (
        "CONTEXT:\n\n"
        "[POL-001#s2] Velocity - Distinguishing card testing\n"
        "Six or more attempts inside 60 seconds whose average amount is 300 "
        "minor units or less constitutes card testing. Card testing must be "
        "blocked immediately.\n\n"
        "TRANSACTION:\n{}\n\n"
        "QUESTION: when do we block card testing?\n"
    )
    answer = await NullLLM().complete(prompt)
    assert answer.startswith("[POL-001#s2]")
    assert "card testing" in answer.lower()


async def test_null_llm_prefers_the_chunk_matching_the_question():
    prompt = (
        "CONTEXT:\n\n"
        "[POL-001#s1] Velocity - Purpose\n"
        "Velocity is measured in sliding windows.\n\n"
        "[POL-003#s1] Review - What a hold means\n"
        "A payment scored into the review band is held: no ledger movement "
        "occurs until an analyst approves it. Holding reserves nothing.\n\n"
        "QUESTION: what does a hold do to my balance?\n"
    )
    answer = await NullLLM().complete(prompt)
    assert answer.startswith("[POL-003#s1]")


async def test_null_llm_says_insufficient_without_chunks():
    answer = await NullLLM().complete("no context here")
    assert "Insufficient evidence" in answer


def test_insufficient_string_is_stable():
    # The validator's replacement string is part of the API contract the
    # dashboard renders; tests pin it so UI copy and backend agree.
    assert INSUFFICIENT.startswith("Insufficient evidence")
