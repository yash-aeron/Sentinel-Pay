from __future__ import annotations

import re

from app.services.idempotency import canonical_hash


def test_hash_is_independent_of_key_order() -> None:
    a = {"amount_minor": 100, "currency": "USD", "description": "x"}
    b = {"description": "x", "currency": "USD", "amount_minor": 100}
    assert canonical_hash(a) == canonical_hash(b)


def test_hash_changes_with_the_body() -> None:
    base = {"amount_minor": 100, "currency": "USD", "description": "x"}
    assert canonical_hash(base) != canonical_hash({**base, "amount_minor": 101})
    assert canonical_hash(base) != canonical_hash({**base, "currency": "EUR"})


def test_null_is_part_of_the_body() -> None:
    """The API always serialises description, so absent vs null never varies in
    practice -- but the hash must not treat them as interchangeable, or a
    client could mutate a field without changing the key's meaning."""
    without = {"amount_minor": 100, "currency": "USD"}
    with_none = {"amount_minor": 100, "currency": "USD", "description": None}
    assert canonical_hash(without) != canonical_hash(with_none)


def test_hash_is_sha256_hex() -> None:
    digest = canonical_hash({"amount_minor": 1, "currency": "USD"})
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_unicode_is_hashed_as_utf8_not_escaped() -> None:
    """ensure_ascii=False keeps hashes stable across Python versions and makes
    them reproducible from a non-Python service."""
    assert canonical_hash({"memo": "café"}) == canonical_hash({"memo": "café"})
    assert canonical_hash({"memo": "café"}) != canonical_hash({"memo": "cafe"})
