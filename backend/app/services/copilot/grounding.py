"""Grounding validator: prove the answer stays inside its evidence.

Enforcement is structural, not a prompt instruction. Two checks:

1. Every chunk citation in the answer must name a chunk in the retrieved
   set -- citing a policy that was never shown is fabrication.
2. Every number in the answer must appear in the retrieved text or in the
   transaction's own data -- invented numbers are the classic RAG failure
   and the most dangerous one for a compliance surface.

An answer that fails either check is replaced wholesale; partial trust is
not a thing here.
"""

from __future__ import annotations

import re

CITATION_RE = re.compile(r"\[([A-Za-z0-9\-]+(?:#[A-Za-z0-9\-]+)?)\]")
NUMBER_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w])")
INSUFFICIENT = (
    "Insufficient evidence in the retrieved policy set to answer this question."
)


def _normalize_number(raw: str) -> str:
    return raw.replace(",", "")


def validate(
    answer: str,
    retrieved_ids: set[str],
    retrieved_text: str,
    allowed_numbers: set[str],
) -> tuple[bool, list[str]]:
    """Return ``(grounded, violations)`` for the candidate answer."""
    violations: list[str] = []

    for citation in CITATION_RE.findall(answer):
        if citation not in retrieved_ids:
            violations.append(f"cited {citation}, which was not retrieved")

    # Numbers inside citation ids (POL-003) are not claims; strip the spans
    # before scanning for invented figures.
    without_citations = CITATION_RE.sub("", answer)
    normalized_context = _normalize_number(retrieved_text)
    for raw in NUMBER_RE.findall(without_citations):
        number = _normalize_number(raw)
        if number in allowed_numbers or number in normalized_context:
            continue
        violations.append(f"number {number} does not appear in the evidence")

    return (not violations, violations)


__all__ = ["INSUFFICIENT", "CITATION_RE", "NUMBER_RE", "validate"]
